"""Authorized formal-batch insight adaptation; no raw comments, models or cache."""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from fractions import Fraction
from threading import BoundedSemaphore
from time import perf_counter

from app.analysis.edu.insights import (
    DiscoveryPolicy,
    GroupRule,
    InsightCandidate,
    discover_candidates,
    metric_sources,
)
from app.analysis.edu.insights.models import Metric
from app.analysis.edu.models import Condition, Dataset, EvidenceKind
from app.core.errors import APIError
from app.importing.edu.types import ParseLimits

from .analysis import CODE_MAP, DIMENSION_MAP, OPERATOR_MAP, AnalysisReader, stat
from .auth import Principal, deny
from .insight_models import (
    InsightCard,
    InsightMetric,
    InsightParticipant,
    InsightQuery,
    InsightRecordsQuery,
    InsightRecordsResult,
    InsightResult,
    InsightSpec,
)
from .models import Model, Student
from .service import _page, fail

RULE_VERSION = "edu.insights.v1"
MAX_RECORDS = ParseLimits().rows_per_sheet // (len(DIMENSION_MAP) + 1)
MAX_WORK = 4_000_000
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_SECONDS = 15.0
_COMPUTE_SLOTS = BoundedSemaphore(2)
LIMITATIONS = (
    "仅供当前获授权教师查看，不代表可公开发布。",
    "评语审核提供器尚未接入，未执行评语分类或一致性复核；扣留不等于空评语或零异常。",
    "观察来自本批结构化评分，不代表能力诊断、因果关系或教学效果。",
)


def metric_view(metric: Metric) -> InsightMetric:
    return InsightMetric(
        key=metric.key,
        value=stat(Fraction(metric.value) if metric.value is not None else None),
        unit=metric.unit,
        denominator=metric.denominator,
        at_score=stat(metric.at_score) if metric.at_score is not None else None,
        source_record_count=len(metric.source_refs),
    )


def card_view(card: InsightCandidate, spec: InsightSpec, batch_id: str) -> InsightCard:
    by_dimension = {DIMENSION_MAP[c.dimension_code]: c for c in spec.conditions}
    return InsightCard(
        candidate_id=card.candidate_id,
        batch_id=batch_id,
        snapshot_id=spec.snapshot_id,
        candidate_type=card.candidate_type,
        dimension_code=CODE_MAP.get(card.query_ref.dimension),
        title=card.title,
        summary=card.summary,
        metrics=tuple(metric_view(m) for m in card.metrics),
        conditions=tuple(by_dimension[c.dimension] for c in card.conditions),
        denominator=card.denominator,
        rule_id=card.rule_id,
        rule_version=card.rule_version,
        status=card.status,
        limitations=(*card.limitations, *LIMITATIONS),
        suggested_next_action=card.suggested_next_action,
        public_restrictions=card.public_restrictions,
    )


class InsightReader(AnalysisReader):
    @contextmanager
    def _snapshot(
        self,
        principal: Principal,
        batch_id: str,
        spec: InsightSpec,
    ) -> Iterator[
        tuple[sqlite3.Connection, Dataset, tuple[Student, ...], tuple[InsightCandidate, ...]]
    ]:
        with self.database.transaction() as connection:
            # Revalidate credential and resource access before snapshot, load, or computation.
            batch = self._batch(connection, principal, batch_id, spec.snapshot_id)
            if not _COMPUTE_SLOTS.acquire(blocking=False):
                raise APIError(503, "INSIGHTS_BUSY", "洞察计算繁忙，请稍后重试")
            started = perf_counter()
            try:
                if batch["student_count"] > MAX_RECORDS:
                    raise APIError(503, "INSIGHT_LIMIT_EXCEEDED", "当前批次超过洞察计算预算")
                data, students = self._load(connection, batch)
                work = len(data.records) * (
                    sum(
                        len({row.score(dim).value for row in data.records})
                        for dim in DIMENSION_MAP.values()
                    )
                    if spec.include_dimensions
                    else 1
                )
                if work > MAX_WORK:
                    raise APIError(503, "INSIGHT_LIMIT_EXCEEDED", "当前批次超过洞察计算预算")
                groups = (
                    (
                        GroupRule(
                            "edu.insight.custom_group.v1",
                            tuple(
                                Condition(
                                    DIMENSION_MAP[c.dimension_code],
                                    OPERATOR_MAP[c.operator],
                                    Fraction(c.value),
                                )
                                for c in spec.conditions
                            ),
                        ),
                    )
                    if spec.conditions
                    else ()
                )
                cards = discover_candidates(
                    data,
                    batch_id=batch_id,
                    snapshot_id=batch["snapshot_id"],
                    groups=groups,
                    policy=DiscoveryPolicy(include_dimensions=spec.include_dimensions),
                    # No provider != empty comments. Never read withheld raw_text here.
                    comments=None,
                )
                if perf_counter() - started > MAX_SECONDS:
                    raise APIError(503, "INSIGHT_TIMEOUT", "洞察计算超时，请稍后重试")
                yield connection, data, students, cards
                if perf_counter() - started > MAX_SECONDS:
                    raise APIError(503, "INSIGHT_TIMEOUT", "洞察计算超时，请稍后重试")
            finally:
                _COMPUTE_SLOTS.release()

    @staticmethod
    def _bounded[T: Model](result: T) -> T:
        if len(result.model_dump_json().encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise APIError(413, "INSIGHT_RESULT_TOO_LARGE", "洞察结果超过响应限制")
        return result

    def query(self, principal: Principal, batch_id: str, query: InsightQuery) -> InsightResult:
        with self._snapshot(principal, batch_id, query) as (_, data, _, cards):
            page = cards[query.offset : query.offset + query.limit]
            return self._bounded(
                InsightResult(
                    batch_id=batch_id,
                    snapshot_id=query.snapshot_id,
                    scope={"kind": "batch", "record_count": len(data.records)},
                    conditions=query.conditions,
                    include_dimensions=query.include_dimensions,
                    rule_version=RULE_VERSION,
                    limitations=LIMITATIONS,
                    total_count=len(cards),
                    items=tuple(card_view(card, query, batch_id) for card in page),
                    page=_page(query.offset, query.limit, len(page), len(cards)),
                )
            )

    def records(
        self,
        principal: Principal,
        batch_id: str,
        candidate_id: str,
        query: InsightRecordsQuery,
    ) -> InsightRecordsResult:
        with self._snapshot(principal, batch_id, query) as (connection, data, students, cards):
            if not re.fullmatch(r"candidate-[0-9a-f]{24}", candidate_id):
                deny(404)
            card = next((card for card in cards if card.candidate_id == candidate_id), None)
            if card is None:
                deny(404)
            metric = next((m for m in card.metrics if m.key == query.metric_key), None)
            if metric is None:
                deny(404)
            refs = metric_sources(card, query.metric_key)
            expected = {row.reference.student_id: row.reference for row in data.records}
            if len(set(refs)) != len(refs) or any(
                expected.get(ref.student_id) != ref
                or ref.batch_id != batch_id
                or ref.source_id != card.query_ref.scope.records[0].source_id
                for ref in refs
            ):
                fail("DATA_INTEGRITY_ERROR", 503)
            dimensions = (
                {card.query_ref.dimension}
                if card.query_ref.dimension
                else {condition.dimension for condition in card.conditions}
            )
            # Reuse the existing verified evidence join, inside this same read transaction.
            rows = connection.execute(self._evidence_rows(), (batch_id,)).fetchall()
            evidence = {row["evidence_id"]: row for row in rows}
            if len(evidence) != len(data.records) * len(DIMENSION_MAP):
                fail("DATA_INTEGRITY_ERROR", 503)
            wanted = {ref.student_id: ref for ref in refs}
            participants = []
            for student in students:  # Stable original batch order, not capability ranking.
                ref = wanted.get(student.student_id)
                if ref is None:
                    continue
                scores = tuple(
                    s for s in student.scores if DIMENSION_MAP[s.dimension_code] in dimensions
                )
                for score in scores:
                    row = evidence.get(score.evidence_id)
                    if row is None:
                        fail("DATA_INTEGRITY_ERROR", 503)
                    proof = self._evidence(row)
                    if (
                        proof.batch_id != batch_id
                        or proof.snapshot_id != query.snapshot_id
                        or proof.student_id != student.student_id
                        or proof.dimension_code != score.dimension_code
                        or proof.score != score.score
                        or not any(
                            r.location == proof.evidence_id
                            and r.source_id == ref.source_id
                            and r.kind is EvidenceKind.SCORE
                            and r.dimension is DIMENSION_MAP[score.dimension_code]
                            for r in ref.evidence_refs
                        )
                    ):
                        fail("DATA_INTEGRITY_ERROR", 503)
                if len(scores) != len(dimensions):
                    fail("DATA_INTEGRITY_ERROR", 503)
                participants.append(
                    InsightParticipant(
                        student_id=student.student_id,
                        display_label=student.display_label,
                        scores=scores,
                    )
                )
            if len(participants) != len(refs):
                fail("DATA_INTEGRITY_ERROR", 503)
            page = participants[query.offset : query.offset + query.limit]
            return self._bounded(
                InsightRecordsResult(
                    batch_id=batch_id,
                    snapshot_id=query.snapshot_id,
                    candidate_id=candidate_id,
                    metric=metric_view(metric),
                    conditions=card_view(card, query, batch_id).conditions,
                    rule_version=card.rule_version,
                    total_count=len(refs),
                    scope_record_count=card.denominator,
                    items=tuple(page),
                    page=_page(query.offset, query.limit, len(page), len(refs)),
                )
            )
