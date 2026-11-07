"""Rule text over replayed comparisons; no model prose, new statistic or signed Fact."""

from dataclasses import dataclass, field

from app.analysis.edu.discovery.models import DimensionDifference, GroupComparison, Policy
from app.analysis.edu.discovery.presentation import exact
from app.analysis.edu.models import Dimension, RecordRef

VERSION = "edu-profile-explanation-0.1-draft"
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


def _sign(d: DimensionDifference) -> int:
    return 1 if d.difference > 0 else -1


def _phrase(d: DimensionDifference) -> str:
    return NAMES[d.dimension] + ("评分相对较高" if d.difference > 0 else "评分相对偏低")


def _feature(d: DimensionDifference, role: str) -> dict:
    return {
        "dimension": d.dimension.name,
        "role": role,
        "direction": _sign(d),
        "target_mean": exact(d.target.mean),
        "remainder_mean": exact(d.remainder.mean),
        "difference": exact(d.difference),
        "target_valid": d.target.valid_count,
        "remainder_valid": d.remainder.valid_count,
        "source_metric_keys": [f"target.{d.dimension.name}", f"remainder.{d.dimension.name}"],
        "source": "verified_five_dimension_summary",
        "new_agent_fact": False,
    }


def explain(
    comparison: GroupComparison,
    policy: Policy,
    label: str,
    previous: tuple[PreviousDescription, ...] = (),
) -> dict:
    """Caller must replay the complete comparison first; this is not an admission API."""
    members = frozenset(comparison.selection.matches)
    overlaps = any(p.members & members for p in previous)
    relevant = [
        p
        for p in previous
        if members
        and p.members
        and len(p.members & members) * 2 >= min(len(p.members), len(members))
    ]

    def repeated(d, role):
        return any((role, d.dimension, _sign(d)) in p.features for p in relevant)

    supported = [
        d
        for d in comparison.dimensions
        if min(d.target.valid_count, d.remainder.valid_count) >= policy.minimum_samples
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
    m, r = len(members), len(comparison.remainder)
    n = comparison.selection.scope.student_count
    unknown = len(comparison.selection.unevaluable_records)
    summary = f"本次{n}人中，{m}人共同满足“{label}”；对照其余可判定的{r}人。"
    if unknown:
        summary += f"另有{unknown}人无法按分组条件判定，未纳入这组对照。"
    for d in highlights:
        direction = "高" if d.difference > 0 else "低"
        summary += (
            f"{NAMES[d.dimension]}均分{float(d.target.mean):.2f}分，"
            f"其余{float(d.remainder.mean):.2f}分，{direction}{float(abs(d.difference)):.2f}分。"
        )
        if d.target.valid_count != m or d.remainder.valid_count != r:
            summary += f"该项有效评分分别为{d.target.valid_count}人和{d.remainder.valid_count}人。"
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
            "substantial_overlap_for_narrative_dedup": bool(relevant),
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
