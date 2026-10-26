"""Positive model disclosure schema, separate from the local teacher presentation.

Never project a present() dictionary or arbitrary metadata/free text. New internal
fields cannot cross this boundary without an explicit whitelist change and tests.
"""

from fractions import Fraction

from app.analysis.edu.discovery.models import (
    BoundaryComparison,
    Discovery,
    GroupComparison,
    Ranking,
    RareCombination,
)
from app.analysis.edu.discovery.presentation import exact
from app.analysis.edu.models import PairCorrelation

from .models import AgentError

LIMITATION_CODES = (
    "exploratory_not_causal",
    "ranking_not_statistical_confidence",
    "synthetic_only_no_teacher_authorization",
)


def model_ranking(rank: Ranking) -> dict:
    robust = rank.robustness
    if robust.method not in {
        "three_fixed_deletions",
        "three_iqr_outer_fence",
        "exact_boundary_membership",
    }:
        raise AgentError("unsupported_model_projection")
    return {
        "score": exact(rank.score),
        "support_count": rank.support_count,
        "completeness": exact(rank.completeness),
        "robustness": {
            "method": robust.method,
            "planned": robust.planned,
            "evaluated": robust.evaluated,
            "passed": robust.passed,
            "value": exact(robust.value),
        },
        "is_statistical_confidence": False,
    }


def model_discovery(discovery: Discovery) -> dict:
    """Construct only aggregate fields from typed computation, never from teacher cards."""
    p = discovery.payload
    n = discovery.context.scope.student_count
    predicates = ()
    if isinstance(p, PairCorrelation):
        metrics = {
            "left": p.left.name,
            "right": p.right.name,
            "rho": exact(p.coefficient),
            "paired_count": p.paired_count,
            "excluded_count": len(p.excluded_records),
            "student_denominator": n,
        }
    elif isinstance(p, GroupComparison):
        selection = p.selection
        outcome = next(d for d in p.dimensions if d.dimension is p.outcome_dimension)
        metrics = {
            "outcome_dimension": outcome.dimension.name,
            "target_count": len(selection.matches),
            "remainder_count": len(p.remainder),
            "unevaluable_count": len(selection.unevaluable_records),
            "student_denominator": n,
            "eligible_denominator": selection.eligible_count,
            "share_total": exact(selection.matched_share.proportion),
            "share_eligible": exact(selection.matched_among_eligible.proportion),
            "target_valid": outcome.target.valid_count,
            "remainder_valid": outcome.remainder.valid_count,
            "target_mean": exact(outcome.target.mean),
            "remainder_mean": exact(outcome.remainder.mean),
            "difference": exact(outcome.difference),
        }
        predicates = selection.conditions
    elif isinstance(p, RareCombination):
        metrics = {
            "left": p.left.name,
            "right": p.right.name,
            "pair_count": p.pair_count,
            "excluded_count": p.excluded_count,
            "match_count": len(p.matches),
            "student_denominator": n,
            "share_pairs": exact(Fraction(len(p.matches), p.pair_count)),
            "q1": exact(p.q1),
            "q3": exact(p.q3),
            "lower_fence": exact(p.lower_fence),
            "upper_fence": exact(p.upper_fence),
            "side": p.side,
        }
    elif isinstance(p, BoundaryComparison):
        metrics = {
            "strict_count": len(p.strict.matches),
            "inclusive_count": len(p.inclusive.matches),
            "added_count": len(p.added),
            "unevaluable_count": len(p.strict.unevaluable_records),
            "student_denominator": n,
            "eligible_denominator": p.strict.eligible_count,
            "share_strict_total": exact(p.strict.matched_share.proportion),
            "share_inclusive_total": exact(p.inclusive.matched_share.proportion),
        }
        predicates = p.inclusive.conditions
    else:
        raise AgentError("unsupported_model_projection")
    if discovery.status not in {"displayable", "interpret_cautiously", "insufficient_evidence"}:
        raise AgentError("unsupported_model_projection")
    return {
        "type": discovery.type,
        "status": discovery.status,
        "rule_version": discovery.rule_version,
        "metrics": metrics,
        "conditions": [
            {
                "dimension": c.dimension.name,
                "operator": c.operator.value,
                "threshold": exact(c.threshold),
            }
            for c in predicates
        ],
        "ranking": model_ranking(discovery.ranking),
        "limitation_codes": LIMITATION_CODES,
    }


def _pick(value: dict, names: tuple[str, ...]) -> dict:
    return {name: value[name] for name in names if name in value}


def _number(value):
    return (
        _pick(value, ("numerator", "denominator", "display", "value", "method"))
        if isinstance(value, dict)
        else value
    )


def _dimension(value: dict) -> dict:
    result = _pick(
        value,
        (
            "dimension",
            "state",
            "valid_count",
            "missing_count",
            "invalid_count",
            "has_variance",
            "exploration_thresholds",
        ),
    )
    for name in ("mean", "median"):
        if name in value:
            result[name] = _number(value[name])
    return result


def model_investigation(tool: str, view: dict) -> dict:
    """Whitelist every investigation shape too; never copy nested internal dictionaries."""
    if tool == "get_dimension_statistics":
        return {
            **_pick(view, ("state", "student_denominator")),
            "dimensions": [_dimension(d) for d in view["dimensions"]],
        }
    if tool == "query_dimension_relation":
        return _pick(
            view,
            (
                "state",
                "left",
                "right",
                "rho",
                "paired_count",
                "excluded_count",
                "student_denominator",
            ),
        )
    if tool == "compare_student_groups":
        differences = []
        for d in view["differences"]:
            projected = _pick(d, ("dimension", "target_valid", "remainder_valid"))
            for name in ("target_mean", "remainder_mean", "difference"):
                projected[name] = _number(d[name])
            differences.append(projected)
        return {
            **_pick(
                view,
                (
                    "target_count",
                    "remainder_count",
                    "unevaluable_count",
                    "student_denominator",
                    "eligible_denominator",
                ),
            ),
            "differences": differences,
        }
    if tool == "inspect_rare_combination":
        return _pick(view, ("state", "paired_count"))
    if tool == "verify_threshold_sensitivity":
        return _pick(
            view,
            (
                "strict_count",
                "inclusive_count",
                "added_count",
                "unevaluable_count",
                "student_denominator",
                "eligible_denominator",
                "observed_boundary",
            ),
        )
    if tool == "inspect_discovery_support":
        result = _pick(view, ("checked_fact_id", "qualification", "status"))
        if "ranking" in view:
            rank = view["ranking"]
            result["ranking"] = {
                **_pick(rank, ("support_count", "is_statistical_confidence")),
                "score": _number(rank["score"]),
                "completeness": _number(rank["completeness"]),
                "robustness": {
                    **_pick(rank["robustness"], ("method", "planned", "evaluated", "passed")),
                    "value": _number(rank["robustness"]["value"]),
                },
            }
        return result
    raise AgentError("unsupported_model_projection")
