"""Rule text over replayed comparisons; no model prose, new statistic or signed Fact."""

from dataclasses import dataclass, field
from fractions import Fraction

from app.analysis.edu.discovery.models import GroupComparison, Policy
from app.analysis.edu.discovery.presentation import exact
from app.analysis.edu.models import Dimension, RecordRef
from app.edu.analysis import stat

VERSION = "edu-profile-explanation-0.1.1-draft"
NAMES = {
    Dimension.ALGORITHMS: "算法",
    Dimension.IMPLEMENTATION: "代码实现",
    Dimension.MODELING: "问题建模",
    Dimension.EFFICIENCY: "效率意识",
    Dimension.STRATEGY: "解题策略",
}


@dataclass(frozen=True)
class PreviousDescription:
    members: frozenset[RecordRef] = field(repr=False)
    # Definition features are not discoveries; keep the roles distinct when avoiding repetition.
    features: frozenset[tuple[str, Dimension, int]]


@dataclass(frozen=True)
class DisplayDifference:
    dimension: Dimension
    condition_defined: bool
    target_mean: Fraction | None
    remainder_mean: Fraction | None
    difference: Fraction | None
    target_valid: int
    remainder_valid: int


@dataclass(frozen=True)
class ExplanationInput:
    total: int
    target: int
    remainder: int
    unknown: int
    dimensions: tuple[DisplayDifference, ...]
    overlaps_previous: bool = False
    relevant_previous: bool = False
    repeated_features: frozenset[tuple[str, Dimension, int]] = frozenset()


def display_score(value: Fraction | None) -> str:
    """Same exact HALF_UP policy as formal statistics; never round before filtering."""
    return "未提供" if value is None else stat(value).display


def explanation_input(
    view: dict, *, overlaps=False, relevant=False, repeated_features=frozenset()
) -> ExplanationInput:
    """Whitelist a POST-disclosure view, not an admission or authorization API.

    No histogram, member, Witness, source locator or raw comparison crosses this seam.
    The engine must replay and disclose the statistics before calling this projector.
    """

    def number(value):
        return None if value is None else Fraction(value["numerator"], value["denominator"])

    metrics = view["metrics"]
    return ExplanationInput(
        metrics["student_denominator"],
        metrics["target_count"],
        metrics["remainder_count"],
        metrics["unevaluable_count"],
        tuple(
            DisplayDifference(
                Dimension[row["dimension"]],
                row["condition_defined"],
                number(row["target"]["mean"]),
                number(row["remainder"]["mean"]),
                number(row["difference"]),
                row["target"]["valid_count"],
                row["remainder"]["valid_count"],
            )
            for row in view["chart"]
        ),
        overlaps,
        relevant,
        repeated_features,
    )


def _sign(d: DisplayDifference) -> int:
    return 1 if d.difference > 0 else -1


def _phrase(d: DisplayDifference) -> str:
    return NAMES[d.dimension] + ("评分相对较高" if d.difference > 0 else "评分相对偏低")


def _feature(d: DisplayDifference, role: str) -> dict:
    return {
        "dimension": d.dimension.name,
        "role": role,
        "direction": _sign(d),
        "target_mean": exact(d.target_mean),
        "remainder_mean": exact(d.remainder_mean),
        "difference": exact(d.difference),
        "target_valid": d.target_valid,
        "remainder_valid": d.remainder_valid,
        "source_metric_keys": [f"target.{d.dimension.name}", f"remainder.{d.dimension.name}"],
        "source": "verified_five_dimension_summary",
        "new_agent_fact": False,
    }


def explain(comparison: ExplanationInput, policy: Policy, label: str) -> dict:
    """Render only whitelisted permitted aggregates; no raw comparison is accepted."""
    if type(comparison) is not ExplanationInput:
        raise TypeError("permitted_explanation_input_required")
    overlaps = comparison.overlaps_previous

    def repeated(d, role):
        return (role, d.dimension, _sign(d)) in comparison.repeated_features

    supported = [
        d
        for d in comparison.dimensions
        if min(d.target_valid, d.remainder_valid) >= policy.minimum_samples
        and d.difference is not None
        and abs(d.difference) >= policy.mean_difference
    ]
    contrasts = sorted(
        (d for d in supported if not d.condition_defined),
        key=lambda d: (-abs(d.difference), list(Dimension).index(d.dimension)),
    )
    fresh = [d for d in contrasts if not repeated(d, "non_condition_difference")]
    highlights = fresh[:2] if fresh else contrasts[:1]
    anchors = sorted(
        (d for d in supported if d.condition_defined),
        key=lambda d: (
            repeated(d, "group_definition"),
            -abs(d.difference),
            list(Dimension).index(d.dimension),
        ),
    )
    anchor = anchors[0] if anchors and not repeated(anchors[0], "group_definition") else None
    only_repeated = bool(contrasts and not fresh)
    title_parts = [_phrase(anchor)] if anchor is not None else []
    title_parts += [_phrase(d) for d in highlights[:1]]
    title = "、".join(title_parts) + "的一组参评者" if title_parts else "本次测评的一组参评者"
    if only_repeated:
        title = _phrase(highlights[0]) + "的参评者（方向与前述群体相近）"
    m, r = comparison.target, comparison.remainder
    n, unknown = comparison.total, comparison.unknown
    summary = f"本次{n}人中，{m}人共同满足“{label}”；对照其余可判定的{r}人。"
    if unknown:
        summary += f"另有{unknown}人无法按分组条件判定，未纳入这组对照。"
    for d in highlights:
        direction = "高" if d.difference > 0 else "低"
        summary += (
            f"{NAMES[d.dimension]}均分{display_score(d.target_mean)}分，"
            f"其余{display_score(d.remainder_mean)}分，{direction}{display_score(abs(d.difference))}分。"
        )
        if d.target_valid != m or d.remainder_valid != r:
            summary += f"该项有效评分分别为{d.target_valid}人和{d.remainder_valid}人。"
    if overlaps:
        summary += "与已展示群体有成员重叠，人数不能相加。"
    if only_repeated:
        summary += "这一方向已在重叠群体出现，不重复作为新增调查结论。"
    elif any(d not in fresh for d in contrasts):
        summary += "与前述群体重复的突出方向不再展开，完整五维对照仍保留。"
    names = "、".join(NAMES[d.dimension] for d in highlights)
    return {
        "title": title,
        "common_pattern": (
            f"共同条件：{label}；相对表现指本组均分与其余可判定参评者的对照，并非人人相同。"
        ),
        "summary": summary,
        "why_show": (
            f"{names}未用于分组，这些评分对照补充了分组条件以外的信息，可先核对相应记录；不能据此解释原因。"
            if highlights and not only_repeated
            else "这一组也呈现已描述过的同方向评分现象；可核对条件与记录，不重复强调相同方向。"
            if only_repeated
            else "暂缺足够有效的非分组评分对照，不生成能力解释。"
        ),
        "suggested_next_action": (
            f"先查看{names}的原始评分记录；若需解释原因，还要补充经授权的评价依据。"
            if highlights
            else "先核对完整评分与有效样本，不推断表现原因。"
        ),
        "explanation": {
            "version": VERSION,
            "origin": "rule_generated_from_verified_statistics",
            "model_generated": False,
            "comparison": "target_vs_remaining_evaluable",
            "anchor": _feature(anchor, "group_definition") if anchor is not None else None,
            "highlights": [_feature(d, "non_condition_difference") for d in highlights],
            "overlaps_previous": overlaps,
            "substantial_overlap_for_narrative_dedup": comparison.relevant_previous,
            "only_repeated_directions": only_repeated,
            "omitted_repeated_dimensions": [
                d.dimension.name for d in contrasts if d not in fresh and d not in highlights
            ],
            "auxiliary_features_are_descriptive_not_new_agent_facts": True,
            "additional_robustness_claim": False,
            "novelty_rule": "half_smaller_group_overlap_same_dimension_direction_role",
        },
    }


def description_context(comparison: GroupComparison, profile: dict) -> PreviousDescription:
    explanation = profile["explanation"]
    selected = explanation["highlights"] + (
        [explanation["anchor"]] if explanation["anchor"] else []
    )
    return PreviousDescription(
        frozenset(comparison.selection.matches),
        frozenset((f["role"], Dimension[f["dimension"]], f["direction"]) for f in selected),
    )
