"""Assemble existing qualified comparisons into coherent, replayable group profiles."""

import json
from dataclasses import dataclass, field, replace
from decimal import Decimal
from fractions import Fraction

from app.ai.edu.discovery.disclosure import check_disclosure
from app.ai.edu.discovery.models import AgentError
from app.analysis.edu import prepare_dataset
from app.analysis.edu.discovery import TargetedDiscovery, discover
from app.analysis.edu.discovery.models import Discovery, GroupComparison, Policy
from app.analysis.edu.discovery.presentation import present
from app.analysis.edu.models import (
    AssessmentRecord,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    ScorePolicy,
)

from prototypes.adaptive_assessment.analysis import Kernel
from prototypes.adaptive_assessment.synthetic import AdmittedSynthetic

from .explanations import PreviousDescription, explain
from .synthetic import RECIPES

VERSION = "edu-capability-profile-0.1-draft"
DIMS = tuple(Dimension)
ITEMS = tuple(f"I{i + 1:02}" for i in range(len(DIMS)))
CODES = (
    "algorithm_data_structure",
    "code_implementation",
    "problem_modeling",
    "efficiency_awareness",
    "solution_strategy",
)
POLICY = Policy(search_budget=50, maximum_findings=50)


def decimal_text(value: Fraction) -> str:
    return format(Decimal(value.numerator) / Decimal(value.denominator), "f")


def dataset_from_source(source: AdmittedSynthetic):
    if type(source) is not AdmittedSynthetic:
        raise AgentError("registered_synthetic_source_required")
    if source.recipe not in (*RECIPES, "five", "constant17"):
        raise AgentError("five_dimension_profile_source_required")
    source.validate()
    kernel = Kernel(source.envelope)
    if tuple(kernel.items) != ITEMS or any(
        item.kind != "competency"
        or item.scale is None
        or (item.scale.minimum, item.scale.maximum, item.scale.step) != ("1", "5", "0.01")
        or kernel.levels[item.item_id] != "ordinal"
        for item in kernel.items.values()
    ):
        raise AgentError("five_dimension_profile_source_required")
    columns = {
        item: {pid: (v, s) for pid, v, s in col.values} for item, col in kernel.columns.items()
    }
    records = []
    for pid in kernel.participants:
        scores, evidence = {}, []
        for dim, item in zip(DIMS, ITEMS, strict=True):
            value, state = columns[item][pid]
            scores[dim] = (
                value if state == "valid" else None if state == "missing" else "unavailable"
            )
            if state == "valid":
                ref = kernel.source("mapping", item, (pid,)).references[0]
                evidence.append(EvidenceRef(source.fingerprint, ref, EvidenceKind.SCORE, dim))
        records.append(
            AssessmentRecord(
                kernel.data.batch.batch_id,
                pid,
                "record-" + pid,
                source.fingerprint,
                scores,
                tuple(evidence),
            )
        )
    return prepare_dataset(records, policy=ScorePolicy(step=Fraction(1, 100))), kernel


def source_sets(discovery: Discovery) -> list[frozenset]:
    return [frozenset(r.student_id for r in s.records) for s in discovery.sources]


def query(discovery: Discovery) -> dict:
    payload = discovery.payload
    return {
        "conditions": [
            {
                "item_id": ITEMS[DIMS.index(c.dimension)],
                "operator": c.operator.value,
                "threshold": decimal_text(c.threshold),
            }
            for c in payload.selection.conditions
        ],
        "outcomes": [ITEMS[DIMS.index(payload.outcome_dimension)]],
    }


@dataclass(frozen=True)
class ProfileCandidate:
    profile_id: str
    discovery: Discovery = field(repr=False)
    equivalent_discovery_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProfileBook:
    source: AdmittedSynthetic = field(repr=False)
    candidates: tuple[ProfileCandidate, ...]
    audit: dict

    def verify(self, profile_id: str) -> ProfileCandidate:
        candidate = next((c for c in self.candidates if c.profile_id == profile_id), None)
        if candidate is None:
            raise AgentError("unknown_profile_id")
        self.audit["projection_source_replays"] = self.audit.get("projection_source_replays", 0) + 1
        dataset, _ = dataset_from_source(self.source)
        original = candidate.discovery
        if original.context.snapshot_id != self.source.fingerprint:
            raise AgentError("profile_snapshot_mismatch")
        builder = TargetedDiscovery(
            dataset,
            batch_id=original.context.batch_id,
            snapshot_id=self.source.fingerprint,
            policy=original.policy,
        )
        comparison, findings = builder.group(
            original.payload.selection.conditions, (original.payload.outcome_dimension,)
        )
        if (
            replace(comparison, outcome_dimension=original.payload.outcome_dimension)
            != original.payload
            or original not in findings
        ):
            raise AgentError("profile_source_replay_failed")
        present(original)  # Reuse exact denominator, distribution and actual-source checks.
        return candidate


def build_profiles(source: AdmittedSynthetic) -> ProfileBook:
    dataset, kernel = dataset_from_source(source)
    result = discover(
        dataset, batch_id=kernel.data.batch.batch_id, snapshot_id=source.fingerprint, policy=POLICY
    )
    by_partition = {}
    rejected = {"support_or_stability": 0, "near_duplicate": 0, "privacy_limited": 0}
    for finding in result.findings:
        if not isinstance(finding.payload, GroupComparison):
            continue
        p = finding.payload
        n = p.selection.scope.student_count
        m = len(p.selection.matches)
        robust = finding.ranking.robustness
        if (
            not n
            or not Fraction(1, 5) <= Fraction(m, n) <= Fraction(9, 10)
            or (robust.passed != 3 or robust.evaluated != 3)
        ):
            rejected["support_or_stability"] += 1
            continue
        partition = frozenset((frozenset(p.selection.matches), frozenset(p.remainder)))
        by_partition.setdefault(partition, []).append(finding)
    # Prefer a short, reproducible description. A complement is shown in the same card.
    groups = sorted(
        by_partition.values(),
        key=lambda group: (
            -max(d.ranking.score for d in group),
            min(d.discovery_id for d in group),
        ),
    )
    chosen, accepted_sets = [], []
    for group in groups:
        group.sort(
            key=lambda d: (
                len(d.payload.selection.conditions),
                -d.ranking.score,
                json.dumps(query(d), sort_keys=True),
            )
        )
        finding = group[0]
        members = frozenset(finding.payload.selection.matches)
        if any(
            Fraction(len(members & old), len(members | old)) >= Fraction(4, 5) for old, _ in chosen
        ):
            rejected["near_duplicate"] += 1
            continue
        try:
            checked = check_disclosure(
                source_sets(finding),
                released=[frozenset(kernel.participants)],
                staged=accepted_sets,
                minimum_cell=5,
            )
        except AgentError:
            rejected["privacy_limited"] += 1
            continue
        accepted_sets.extend(checked)
        chosen.append(
            (
                members,
                ProfileCandidate(
                    "profile-" + finding.discovery_id,
                    finding,
                    tuple(sorted(d.discovery_id for d in group)),
                ),
            )
        )
        if len(chosen) == 6:
            break
    return ProfileBook(
        source,
        tuple(c for _, c in chosen),
        {
            "search_budget": POLICY.search_budget,
            "search_attempts": len(result.search_audit),
            "raw_qualified_findings": result.qualified_count,
            "returned_findings": len(result.findings),
            "upstream_truncated": result.qualified_count > len(result.findings),
            "group_discoveries": sum(
                isinstance(d.payload, GroupComparison) for d in result.findings
            ),
            "equivalent_group_cards_collapsed": sum(len(g) - 1 for g in by_partition.values()),
            "representative_share_minimum": "1/5",
            "distinct_partitions": len(by_partition),
            "profile_candidates": len(chosen),
            "filters": rejected,
            "search_is_exhaustive": False,
            "rare_combinations": sum(d.type == "rare_combination" for d in result.findings),
            "rare_individuals_not_promoted_to_typical_groups": True,
        },
    )


def teacher_view(
    book: ProfileBook, profile_id: str, *, previous: tuple[PreviousDescription, ...] = ()
) -> dict:
    candidate = book.verify(profile_id)
    d = candidate.discovery
    p = d.payload
    view = present(d)
    conditions = [
        {
            "dimension_code": CODES[DIMS.index(c.dimension)],
            "operator": c.operator.value,
            "value": decimal_text(c.threshold),
        }
        for c in p.selection.conditions
    ]
    label = "、".join(
        f"{c.dimension.value}{c.operator.value.replace('<=', '≤').replace('>=', '≥')}"
        f"{decimal_text(c.threshold)}分"
        for c in p.selection.conditions
    )
    return {
        "profile_id": profile_id,
        "batch_id": d.context.batch_id,
        "snapshot_id": d.context.snapshot_id,
        "rule_version": VERSION,
        "calculation_rule_version": d.rule_version,
        **explain(p, d.policy, label, previous),
        "metrics": view["metrics"],
        "dimensions": [
            dict(
                row,
                dimension_code=CODES[DIMS.index(Dimension[row["dimension"]])],
                label=Dimension[row["dimension"]].value,
            )
            for row in view["chart"]
        ],
        "conditions": conditions,
        "status": d.status,
        "exploration_query": {"snapshot_id": d.context.snapshot_id, "conditions": conditions},
        "source_queries": [
            {"profile_id": profile_id, "metric_key": s.metric_key} for s in d.sources
        ],
        "source_replay_verified": True,
        "five_dimension_summary_origin": "deterministic_discovery_replay",
        "agent_fact_scope": "primary_non_condition_outcome_only",
        "comment_state": "unavailable_not_authorized",
        "limitations": [
            "评分画像不是综合能力等级；筛选维度的差异由条件定义。",
            "群体可能重叠，人数不能相加；未覆盖全部候选。",
            "未读取评语或任务原文，不推断具体代码错误、心理或因果。",
        ],
        "public_display_allowed": False,
        "production_enabled": False,
    }


def resolve(book: ProfileBook, profile_id: str, metric_key: str) -> tuple:
    candidate = book.verify(profile_id)
    metric = next((s for s in candidate.discovery.sources if s.metric_key == metric_key), None)
    if metric is None:
        raise AgentError("unknown_profile_metric")
    _, kernel = dataset_from_source(book.source)
    witnesses = []
    for record in metric.records:
        for dim in metric.dimensions:
            witnesses.extend(
                kernel.witnesses[ref]
                for ref in kernel.source(
                    metric_key, ITEMS[DIMS.index(dim)], (record.student_id,)
                ).references
            )
    return tuple(witnesses)
