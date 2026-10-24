"""Explicit aggregate-only projection. Public release is denied unless individually approved."""

from fractions import Fraction

from ..models import AnalysisInputError, Dimension
from .models import InsightCandidate

FRONTEND_DIMENSIONS = {
    Dimension.ALGORITHMS: "algorithm",
    Dimension.IMPLEMENTATION: "coding",
    Dimension.MODELING: "modeling",
    Dimension.EFFICIENCY: "efficiency",
    Dimension.STRATEGY: "strategy",
}


def _exact(value: int | Fraction | None) -> dict[str, int] | None:
    if value is None:
        return None
    numeric = Fraction(value)
    return {"numerator": numeric.numerator, "denominator": numeric.denominator}


def _rounded(value: Fraction, digits: int) -> str:
    scale = 10**digits
    whole, remainder = divmod(abs(value.numerator) * scale, value.denominator)
    whole += remainder * 2 >= value.denominator
    sign = "-" if value < 0 and whole else ""
    if not digits:
        return sign + str(whole)
    return f"{sign}{whole // scale}.{whole % scale:0{digits}d}"


def _card(
    candidate: InsightCandidate, digits: int, *, teacher: bool, approved: bool
) -> dict[str, object]:
    metrics = []
    for metric in candidate.metrics:
        if metric.value is None:
            display = "暂无有效数据"
        elif metric.unit == "ratio":
            display = _rounded(Fraction(metric.value) * 100, digits) + "%"
        elif metric.unit == "score":
            display = _rounded(Fraction(metric.value), digits)
        else:
            display = str(metric.value)
        metrics.append(
            {
                "key": metric.key,
                "unit": metric.unit,
                "exact": _exact(metric.value),
                "display": display,
                "denominator": metric.denominator,
                "at_score_exact": _exact(metric.at_score),
            }
        )
    return {
        "candidate_id": candidate.candidate_id,
        "batch_id": candidate.batch_id,
        "candidate_type": candidate.candidate_type,
        "scope": {"kind": "selected_assessment_records", "record_count": candidate.denominator},
        "dimension": candidate.query_ref.dimension.name if candidate.query_ref.dimension else None,
        "frontend_dimension": FRONTEND_DIMENSIONS[candidate.query_ref.dimension]
        if candidate.query_ref.dimension
        else None,
        "title": candidate.title,
        "summary": candidate.summary,
        "metrics": metrics,
        "conditions": [
            {
                "dimension": item.dimension.name,
                "frontend_dimension": FRONTEND_DIMENSIONS[item.dimension],
                "operator": item.operator.value,
                "frontend_operator": "=" if item.operator.value == "==" else item.operator.value,
                "threshold_exact": _exact(item.threshold),
            }
            for item in candidate.conditions
        ],
        "denominator": candidate.denominator,
        "rule_id": candidate.rule_id,
        "rule_version": candidate.rule_version,
        "adapter_version": candidate.adapter_version,
        "source_rule_fingerprint": candidate.source_rule_fingerprint,
        "status": candidate.status,
        "limitations": candidate.limitations,
        "suggested_next_action": candidate.suggested_next_action
        if teacher
        else ("仅查看经审核的汇总口径；个人详情须另行授权。"),
        "public_display_allowed": approved and not candidate.public_restrictions,
        "public_restrictions": candidate.public_restrictions,
        "controlled_query": {
            "candidate_id": candidate.candidate_id,
            "kind": candidate.query_ref.kind,
            "snapshot_id": candidate.query_ref.snapshot_id,
            "metric_keys": tuple(item.key for item in candidate.metrics),
        }
        if teacher
        else None,
    }


def _validate_digits(digits: int) -> None:
    if type(digits) is not int or not 0 <= digits <= 6:
        raise AnalysisInputError("display_digits_between_zero_and_six_required")


def teacher_cards(
    candidates: tuple[InsightCandidate, ...],
    *,
    authorization_checked: bool = False,
    digits: int = 2,
    public_approved_ids: frozenset[str] = frozenset(),
) -> tuple[dict[str, object], ...]:
    """Caller asserts its own real authorization; this module does not authenticate teachers."""
    _validate_digits(digits)
    if authorization_checked is not True:
        raise AnalysisInputError("teacher_authorization_required")
    return tuple(
        _card(
            candidate, digits, teacher=True, approved=candidate.candidate_id in public_approved_ids
        )
        for candidate in candidates
    )


def public_cards(
    candidates: tuple[InsightCandidate, ...],
    *,
    approved_ids: frozenset[str] = frozenset(),
    digits: int = 2,
) -> tuple[dict[str, object], ...]:
    """No approval or an unsafe cell means no card, not a shell leaking private counts."""
    _validate_digits(digits)
    return tuple(
        _card(candidate, digits, teacher=False, approved=True)
        for candidate in candidates
        if candidate.candidate_id in approved_ids and not candidate.public_restrictions
    )
