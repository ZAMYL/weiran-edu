"""Complete authorized inputs for a future deterministic engine, with no engine imports."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from threading import BoundedSemaphore
from time import perf_counter

from app.analysis.edu import select_students
from app.analysis.edu.models import Condition, Dataset, Dimension, EvidenceKind, RecordRef
from app.core.errors import APIError
from app.core.exact_json import exact_json_bytes
from app.importing.edu.preview import validate_page
from app.importing.edu.types import ParseLimits

from .analysis import DIMENSION_MAP, OPERATOR_MAP, AnalysisReader
from .auth import Principal, deny
from .discovery_models import (
    DiscoveryList,
    DiscoveryParticipants,
    DiscoveryScope,
    DiscoverySpec,
    DiscoverySummary,
)
from .insight_models import InsightParticipant
from .models import Model, Student
from .service import _page, fail

MAX_RECORDS = ParseLimits().rows_per_sheet // 6
MAX_SECONDS = 15.0
MAX_RESPONSE_BYTES = 1024 * 1024
SLOTS = BoundedSemaphore(2)


@dataclass(frozen=True, repr=False)
class DiscoveryInput:
    """Backend-only object. IDs/references are facts, never authorization capabilities."""

    scope: DiscoveryScope
    spec: DiscoverySpec
    dataset: Dataset
    student_ids: tuple[str, ...]
    students: tuple[Student, ...]


class DiscoveryInputReader(AnalysisReader):
    def _load_input(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        batch_id: str,
        spec: DiscoverySpec,
    ) -> DiscoveryInput:
        batch = self._batch(connection, principal, batch_id, spec.snapshot_id)
        if batch["student_count"] > MAX_RECORDS:
            raise APIError(503, "DISCOVERY_LIMIT_EXCEEDED", "批次超过发现计算预算")
        dataset, students = self._load(connection, batch)
        conditions = tuple(sorted(spec.scope_conditions, key=lambda c: c.dimension_code))
        spec = spec.model_copy(update={"scope_conditions": conditions})
        if conditions:
            selected = select_students(
                dataset,
                batch_id=batch_id,
                conditions=tuple(
                    Condition(
                        DIMENSION_MAP[c.dimension_code], OPERATOR_MAP[c.operator], Fraction(c.value)
                    )
                    for c in conditions
                ),
            )
            ids = selected.student_ids
            eligible, unknown = selected.eligible_count, len(selected.unevaluable_records)
        else:
            ids = tuple(r.reference.student_id for r in dataset.records)
            eligible, unknown = len(ids), 0
        canonical = {
            "batch": batch_id,
            "snapshot": spec.snapshot_id,
            "source": batch["file_sha256"],
            "profile": spec.profile,
            "conditions": [(c.dimension_code, c.operator, int(c.value * 100)) for c in conditions],
        }
        key = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        statistics = self._statistics(
            batch, dataset, student_ids=ids, kind="matched_group" if conditions else "batch"
        )
        return DiscoveryInput(
            DiscoveryScope(
                batch_id=batch_id,
                snapshot_id=spec.snapshot_id,
                input_key=key,
                scope_conditions=conditions,
                batch_record_count=len(dataset.records),
                scope_record_count=len(ids),
                eligible_count=eligible,
                unevaluable_count=unknown,
                statistics=statistics,
            ),
            spec,
            dataset,
            ids,
            students,
        )

    @contextmanager
    def snapshot(
        self, principal: Principal, batch_id: str, spec: DiscoverySpec
    ) -> Iterator[DiscoveryInput]:
        # Authorize before allocating compute capacity. Do not hold SQLite read locks during work.
        with self.database.transaction() as connection:
            self._batch(connection, principal, batch_id, spec.snapshot_id)
        if not SLOTS.acquire(blocking=False):
            raise APIError(503, "DISCOVERY_BUSY", "发现计算繁忙，请稍后重试")
        started = perf_counter()
        try:
            with self.database.transaction() as connection:
                original = self._load_input(connection, principal, batch_id, spec)
            yield original
            # A fresh transaction observes token/grant/member revocation and corruption during work.
            with self.database.transaction() as connection:
                current = self._load_input(connection, principal, batch_id, spec)
                if current != original:
                    fail("DATA_INTEGRITY_ERROR", 503)
            if perf_counter() - started > MAX_SECONDS:
                raise APIError(503, "DISCOVERY_TIMEOUT", "发现计算超时")
        finally:
            SLOTS.release()

    @staticmethod
    def bounded[T: Model](value: T) -> T:
        if len(exact_json_bytes(value.model_dump(mode="json"))) > MAX_RESPONSE_BYTES:
            raise APIError(413, "DISCOVERY_RESPONSE_TOO_LARGE", "发现结果超过响应预算")
        return value

    def page(
        self,
        scope: DiscoveryScope,
        findings: tuple[DiscoverySummary, ...],
        *,
        qualified_count: int,
        rule_version: str,
        offset: int = 0,
        limit: int = 20,
    ) -> DiscoveryList:
        validate_page(offset, limit)
        if limit > 50 or len(findings) > 50:
            raise APIError(413, "DISCOVERY_LIMIT_EXCEEDED", "发现结果超过预算")
        if qualified_count < len(findings) or len({f.discovery_id for f in findings}) != len(
            findings
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        if any(
            (f.batch_id, f.snapshot_id, f.input_key, f.rule_version)
            != (scope.batch_id, scope.snapshot_id, scope.input_key, rule_version)
            for f in findings
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        items = findings[offset : offset + limit]
        return self.bounded(
            DiscoveryList(
                scope=scope,
                rule_version=rule_version,
                qualified_count=qualified_count,
                retained_count=len(findings),
                truncated=qualified_count > len(findings),
                items=items,
                page=_page(offset, limit, len(items), len(findings)),
            )
        )

    @staticmethod
    def finding(findings: tuple[DiscoverySummary, ...], discovery_id: str) -> DiscoverySummary:
        found = next((f for f in findings if f.discovery_id == discovery_id), None)
        if found is None:
            deny(404)
        return found

    def resolve_sources(
        self,
        principal: Principal,
        original: DiscoveryInput,
        refs: tuple[RecordRef, ...],
        dimensions: tuple[Dimension, ...],
        *,
        metric_key: str,
        offset: int = 0,
        limit: int = 20,
    ) -> DiscoveryParticipants:
        """Refs/dimensions MUST come from the server's metric resolver, never an HTTP body."""
        validate_page(offset, limit)
        if limit > 50:
            raise APIError(413, "DISCOVERY_LIMIT_EXCEEDED", "发现结果超过预算")
        with self.database.transaction() as connection:
            current = self._load_input(
                connection, principal, original.scope.batch_id, original.spec
            )
            if current != original or not dimensions or len(set(dimensions)) != len(dimensions):
                fail("DATA_INTEGRITY_ERROR", 503)
            canonical = {r.reference.student_id: r.reference for r in current.dataset.records}
            if len({r.student_id for r in refs}) != len(refs) or any(
                r.student_id not in current.student_ids or canonical.get(r.student_id) != r
                for r in refs
            ):
                fail("DATA_INTEGRITY_ERROR", 503)
            rows = connection.execute(self._evidence_rows(), (current.scope.batch_id,)).fetchall()
            evidence = {row["evidence_id"]: row for row in rows}
            if len(evidence) != len(current.dataset.records) * 5:
                fail("DATA_INTEGRITY_ERROR", 503)
            wanted = {r.student_id: r for r in refs}
            participants = []
            for student in current.students:
                if student.student_id not in wanted:
                    continue
                scores = tuple(
                    s for s in student.scores if DIMENSION_MAP[s.dimension_code] in dimensions
                )
                if len(scores) != len(dimensions):
                    fail("DATA_INTEGRITY_ERROR", 503)
                for score in scores:
                    proof = self._evidence(evidence[score.evidence_id])
                    if (
                        proof.batch_id,
                        proof.student_id,
                        proof.snapshot_id,
                        proof.dimension_code,
                        proof.score,
                    ) != (
                        current.scope.batch_id,
                        student.student_id,
                        current.scope.snapshot_id,
                        score.dimension_code,
                        score.score,
                    ) or not any(
                        e.location == proof.evidence_id
                        and e.source_id == wanted[student.student_id].source_id
                        and e.kind is EvidenceKind.SCORE
                        and e.dimension is DIMENSION_MAP[score.dimension_code]
                        for e in wanted[student.student_id].evidence_refs
                    ):
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
            items = tuple(participants[offset : offset + limit])
            return self.bounded(
                DiscoveryParticipants(
                    scope=current.scope,
                    metric_key=metric_key,
                    total_count=len(refs),
                    items=items,
                    page=_page(offset, limit, len(items), len(refs)),
                )
            )
