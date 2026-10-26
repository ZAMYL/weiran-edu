"""Aggregate-only projection; controlled sources remain internal to an authorized caller."""

from collections import Counter
from dataclasses import asdict
from fractions import Fraction

from ..models import (
    AnalysisInputError,
    Condition,
    DimensionStats,
    EvidenceKind,
    EvidenceRef,
    PairCorrelation,
    RecordRef,
)
from .models import BoundaryComparison, Discovery, GroupComparison, RareCombination


def exact(value: Fraction | float | int | None) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, float):
        return {"value": value, "method": "stdlib_float"}
    value = Fraction(value)
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "display": f"{float(value):.4f}",
    }


def _stats(stats: DimensionStats, total: int) -> dict[str, object]:
    if (
        stats.valid_count != len(set(stats.valid_records))
        or stats.valid_count + stats.missing_count + stats.invalid_count != total
        or any(n < 0 for n in (stats.valid_count, stats.missing_count, stats.invalid_count))
        or sum(f.share.count for f in stats.distribution) != stats.valid_count
        or any(
            f.share.denominator != stats.valid_count or f.share.count <= 0
            for f in stats.distribution
        )
    ):
        raise AnalysisInputError("inconsistent_discovery_statistics")
    expected = (
        (
            sum((f.score * f.share.count for f in stats.distribution), Fraction(0))
            / stats.valid_count
        )
        if stats.valid_count
        else None
    )
    if stats.mean != expected:
        raise AnalysisInputError("inconsistent_discovery_mean")
    return {
        "valid_count": stats.valid_count,
        "missing_count": stats.missing_count,
        "invalid_count": stats.invalid_count,
        "mean": exact(stats.mean),
        "median": exact(stats.median),
        "distribution": [
            {
                "score": exact(f.score),
                "count": f.share.count,
                "student_denominator": f.share.denominator,
            }
            for f in stats.distribution
        ],
    }


def _conditions(conditions: tuple[Condition, ...]) -> list[dict[str, object]]:
    return [
        {
            "dimension": c.dimension.name,
            "operator": c.operator.value,
            "threshold": exact(c.threshold),
        }
        for c in conditions
    ]


def present(discovery: Discovery) -> dict[str, object]:
    """No identity, record locator, free text, or individual score rows in this view."""
    n = discovery.context.scope.student_count
    payload = discovery.payload
    conditions = []
    if isinstance(payload, PairCorrelation):
        if payload.paired_count + len(payload.excluded_records) != n:
            raise AnalysisInputError("inconsistent_pair_denominator")
        metrics = {
            "left": payload.left.name,
            "right": payload.right.name,
            "rho": exact(payload.coefficient),
            "paired_count": payload.paired_count,
            "excluded_count": len(payload.excluded_records),
            "student_denominator": n,
        }
        counts = Counter((o.left_score, o.right_score) for o in payload.observations)
        chart = [
            {"x": exact(x), "y": exact(y), "count": count}
            for (x, y), count in sorted(counts.items())
        ]
    elif isinstance(payload, GroupComparison):
        if payload.outcome_dimension is not None and not any(
            d.dimension is payload.outcome_dimension and not d.condition_defined
            for d in payload.dimensions
        ):
            raise AnalysisInputError("invalid_group_outcome_dimension")
        selection = payload.selection
        m, r, u = len(selection.matches), len(payload.remainder), len(selection.unevaluable_records)
        groups = [
            set(selection.matches),
            set(payload.remainder),
            set(selection.unevaluable_records),
        ]
        if (
            sum(map(len, groups)) != n
            or set.union(*groups) != set(discovery.context.scope.records)
            or (any(groups[i] & groups[j] for i, j in ((0, 1), (0, 2), (1, 2))))
        ):
            raise AnalysisInputError("inconsistent_disjoint_groups")
        metrics = {
            "outcome_dimension": (
                payload.outcome_dimension.name if payload.outcome_dimension else None
            ),
            "target_count": m,
            "remainder_count": r,
            "unevaluable_count": u,
            "student_denominator": n,
            "eligible_denominator": n - u,
            "share_total": exact(Fraction(m, n)) if n else None,
            "share_eligible": exact(Fraction(m, n - u)) if n - u else None,
        }
        chart = []
        for d in payload.dimensions:
            if (
                not set(d.target.valid_records) <= groups[0]
                or not set(d.remainder.valid_records) <= groups[1]
            ):
                raise AnalysisInputError("inconsistent_metric_sources")
            expected = (
                d.target.mean - d.remainder.mean
                if d.target.mean is not None and d.remainder.mean is not None
                else None
            )
            if d.difference != expected:
                raise AnalysisInputError("inconsistent_group_difference")
            chart.append(
                {
                    "dimension": d.dimension.name,
                    "condition_defined": d.condition_defined,
                    "target": _stats(d.target, m),
                    "remainder": _stats(d.remainder, r),
                    "difference": exact(d.difference),
                }
            )
        conditions = _conditions(selection.conditions)
    elif isinstance(payload, RareCombination):
        if payload.pair_count + payload.excluded_count != n or (
            sum(count for _, count in payload.distribution) != payload.pair_count
            or len(set(payload.pair_records)) != payload.pair_count
            or not set(payload.matches) <= set(payload.pair_records)
        ):
            raise AnalysisInputError("inconsistent_rare_denominator")
        metrics = {
            "left": payload.left.name,
            "right": payload.right.name,
            "pair_count": payload.pair_count,
            "excluded_count": payload.excluded_count,
            "match_count": len(payload.matches),
            "pattern": payload.pattern,
            "share_pairs": exact(Fraction(len(payload.matches), payload.pair_count)),
            "q1": exact(payload.q1),
            "q3": exact(payload.q3),
            "lower_fence": exact(payload.lower_fence),
            "upper_fence": exact(payload.upper_fence),
            "side": payload.side,
            "student_denominator": n,
        }
        chart = [
            {"difference": exact(value), "count": count} for value, count in payload.distribution
        ]
    elif isinstance(payload, BoundaryComparison):
        strict, inclusive = payload.strict, payload.inclusive
        if (
            not set(strict.matches) <= set(inclusive.matches)
            or set(payload.added) != set(inclusive.matches) - set(strict.matches)
            or strict.unevaluable_records != inclusive.unevaluable_records
        ):
            raise AnalysisInputError("inconsistent_boundary_membership")
        metrics = {
            "strict_count": len(strict.matches),
            "inclusive_count": len(inclusive.matches),
            "added_count": len(payload.added),
            "removed_count": 0,
            "student_denominator": n,
            "eligible_denominator": strict.eligible_count,
            "unevaluable_count": len(strict.unevaluable_records),
            "share_strict_total": exact(strict.matched_share.proportion),
            "share_inclusive_total": exact(inclusive.matched_share.proportion),
            "share_strict_eligible": exact(strict.matched_among_eligible.proportion),
            "share_inclusive_eligible": exact(inclusive.matched_among_eligible.proportion),
        }
        conditions = _conditions(inclusive.conditions)
        chart = [
            {"operator": "<", "count": len(strict.matches)},
            {"operator": "<=", "count": len(inclusive.matches)},
        ]
    else:
        raise AnalysisInputError("unknown_discovery_payload")
    rank = discovery.ranking
    return {
        "discovery_id": discovery.discovery_id,
        "type": discovery.type,
        "batch_id": discovery.context.batch_id,
        "snapshot_id": discovery.context.snapshot_id,
        "title": discovery.title,
        "summary": discovery.summary,
        "status": discovery.status,
        "rule_version": discovery.rule_version,
        "rule_id": f"edu.discovery.{discovery.type}",
        "parameters": {
            k: exact(v) if isinstance(v, Fraction) else v
            for k, v in asdict(discovery.policy).items()
        },
        "metrics": metrics,
        "chart": chart,
        "conditions": conditions,
        "limitations": discovery.limitations,
        "ranking": {
            "score": exact(rank.score),
            "effect": exact(rank.effect),
            "support_count": rank.support_count,
            "support": exact(rank.support_component),
            "completeness": exact(rank.completeness),
            "robustness": {
                "method": rank.robustness.method,
                "planned": rank.robustness.planned,
                "evaluated": rank.robustness.evaluated,
                "passed": rank.robustness.passed,
                "value": exact(rank.robustness.value),
            },
            "is_statistical_confidence": False,
        },
        "controlled_queries": [
            {"discovery_id": discovery.discovery_id, "metric_key": s.metric_key}
            for s in discovery.sources
        ],
        "public_display_allowed": False,
    }


def sources(
    discovery: Discovery, metric_key: str
) -> tuple[tuple[RecordRef, tuple[EvidenceRef, ...]], ...]:
    """Actual participants only. Caller must recheck authorization and formal evidence IDs."""
    present(discovery)  # Fail closed on inconsistent denominators/partitions.
    selected = next((s for s in discovery.sources if s.metric_key == metric_key), None)
    if selected is None:
        raise AnalysisInputError("unknown_discovery_metric")
    payload = discovery.payload
    expected = {}
    if isinstance(payload, PairCorrelation):
        expected["paired_scores"] = tuple(o.reference for o in payload.observations)
    elif isinstance(payload, GroupComparison):
        expected["target_members"] = payload.selection.matches
        expected["remainder_members"] = payload.remainder
        for d in payload.dimensions:
            expected[f"target.{d.dimension.name}"] = d.target.valid_records
            expected[f"remainder.{d.dimension.name}"] = d.remainder.valid_records
    elif isinstance(payload, RareCombination):
        expected["rare_members"] = payload.matches
        expected["pair_reference"] = payload.pair_records
    elif isinstance(payload, BoundaryComparison):
        expected["strict_members"] = payload.strict.matches
        expected["inclusive_members"] = payload.inclusive.matches
        expected["added_members"] = payload.added
    if isinstance(payload, PairCorrelation):
        dimensions = (payload.left, payload.right)
    elif isinstance(payload, GroupComparison):
        dimensions = (
            tuple(c.dimension for c in payload.selection.conditions)
            if metric_key.endswith("members")
            else tuple(
                d.dimension
                for d in payload.dimensions
                if metric_key.endswith(f".{d.dimension.name}")
            )
        )
    elif isinstance(payload, RareCombination):
        dimensions = (payload.left, payload.right)
    else:
        dimensions = tuple(c.dimension for c in payload.strict.conditions)
    if (
        metric_key not in expected
        or selected.records != expected[metric_key]
        or selected.dimensions != dimensions
    ):
        raise AnalysisInputError("discovery_metric_source_mismatch")
    if not set(selected.records) <= set(discovery.context.scope.records):
        raise AnalysisInputError("discovery_source_outside_scope")
    return tuple(
        (
            r,
            tuple(
                e
                for e in r.evidence_refs
                if e.kind is EvidenceKind.SCORE
                and e.dimension in selected.dimensions
                and e.source_id == r.source_id
            ),
        )
        for r in selected.records
    )
