"""Reuse exact analytics; bounded exploration never asserts statistical significance."""

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, replace
from fractions import Fraction
from itertools import combinations
from math import ceil
from statistics import median

from ..correlation import spearman_correlations
from ..engine import cohort, dimension_stats, select_students, summarize
from ..models import (
    AnalysisInputError,
    Condition,
    Dataset,
    Dimension,
    Operator,
    Scope,
    ScoreState,
    Summary,
    ValidatedRecord,
    require_id,
)
from .models import (
    VERSION,
    BoundaryComparison,
    BoundaryRequest,
    Context,
    DimensionDifference,
    Discovery,
    DiscoveryResult,
    DiscoveryType,
    GroupComparison,
    Payload,
    Policy,
    Quality,
    Ranking,
    RareCombination,
    Robustness,
    SearchAttempt,
    SourceSet,
    Status,
)

DEFAULT_POLICY = Policy()

COMMON_LIMITS = (
    "探索结果不是因果结论或教学效果证明；排序分数不是统计置信度。",
    "多重探索与评分/缺失偏差未经统计检验校正，需教师核查。",
    "仅限获授权内部使用；小样本不宜公开展示，内部引用不是权限凭证。",
)


def _scope(
    dataset: Dataset,
    batch_id: str,
    student_ids: tuple[str, ...] | None,
    policy: Policy,
) -> tuple[Scope, tuple[ValidatedRecord, ...]]:
    if (dataset.policy.minimum, dataset.policy.maximum, dataset.policy.step) != (
        Fraction(1),
        Fraction(5),
        Fraction(1, 100),
    ):
        raise AnalysisInputError("discovery_requires_v1_cent_score_policy")
    scope, records = cohort(dataset, batch_id, student_ids)
    if len(records) > policy.maximum_students:
        raise AnalysisInputError("discovery_student_budget_exceeded")
    return scope, records


def _conditions(dataset: Dataset, conditions: tuple[Condition, ...]) -> None:
    if not 1 <= len(conditions) <= 2 or any(not isinstance(c, Condition) for c in conditions):
        raise AnalysisInputError("one_or_two_typed_conditions_required")
    if len({c.dimension for c in conditions}) != len(conditions):
        raise AnalysisInputError("distinct_condition_dimensions_required")
    for c in conditions:
        if (
            not (dataset.policy.minimum <= c.threshold <= dataset.policy.maximum)
            or (c.threshold * 100).denominator != 1
        ):
            raise AnalysisInputError("threshold_outside_v1_cent_grid")


def compare_groups(
    dataset: Dataset,
    *,
    batch_id: str,
    conditions: tuple[Condition, ...],
    student_ids: tuple[str, ...] | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> GroupComparison:
    _conditions(dataset, conditions)
    scope, records = _scope(dataset, batch_id, student_ids, policy)
    selection = select_students(
        dataset, batch_id=batch_id, student_ids=scope.student_ids, conditions=conditions
    )
    target_ids = set(selection.student_ids)
    excluded = {r.student_id for r in selection.unevaluable_records}
    target = tuple(r for r in records if r.reference.student_id in target_ids)
    not_remainder = target_ids | excluded
    rest = tuple(r for r in records if r.reference.student_id not in not_remainder)
    dimensions = []
    for d in Dimension:
        a, b = dimension_stats(target, d), dimension_stats(rest, d)
        diff = a.mean - b.mean if a.mean is not None and b.mean is not None else None
        dimensions.append(
            DimensionDifference(d, a, b, diff, d in {c.dimension for c in conditions})
        )
    return GroupComparison(selection, tuple(r.reference for r in rest), tuple(dimensions))


def compare_boundary(
    dataset: Dataset,
    *,
    batch_id: str,
    request: BoundaryRequest,
    student_ids: tuple[str, ...] | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> BoundaryComparison:
    _conditions(dataset, request.conditions)
    scope, records = _scope(dataset, batch_id, student_ids, policy)
    chosen = next((c for c in request.conditions if c.dimension == request.varying_dimension), None)
    if chosen is None or chosen.operator not in (Operator.LT, Operator.LE):
        raise AnalysisInputError("boundary_requires_lt_or_le_condition")
    selections = []
    for op in (Operator.LT, Operator.LE):
        conditions = tuple(
            replace(c, operator=op) if c is chosen else c for c in request.conditions
        )
        selections.append(
            select_students(
                dataset, batch_id=batch_id, student_ids=scope.student_ids, conditions=conditions
            )
        )
    strict, inclusive = selections
    strict_members = set(strict.matches)
    added = tuple(r for r in inclusive.matches if r not in strict_members)
    observed = any(
        r.score(chosen.dimension).state is ScoreState.VALID
        and r.score(chosen.dimension).value == chosen.threshold
        for r in records
    )
    return BoundaryComparison(
        strict,
        inclusive,
        added,
        observed,
        "interpret_cautiously" if strict.eligible_count else "insufficient_evidence",
    )


def _quantile(values: list[Fraction], p: Fraction) -> Fraction:
    return sorted(values)[ceil(len(values) * p) - 1]


def _rules(summary: Summary) -> tuple[tuple[Condition, ...], ...]:
    tails = {}
    for stats in summary.dimensions:
        values = [f.score for f in stats.distribution for _ in range(f.share.count)]
        if values and min(values) != max(values):
            tails[stats.dimension] = (
                Condition(stats.dimension, Operator.LE, _quantile(values, Fraction(1, 4))),
                Condition(stats.dimension, Operator.GE, _quantile(values, Fraction(3, 4))),
            )
    rules = [(c,) for pair in tails.values() for c in pair]
    pairs = tuple(combinations(tails, 2))
    orientations = ((0, 0), (0, 1), (1, 0), (1, 1))
    for round_index in range(4):
        for pair_index, (left, right) in enumerate(pairs):
            a, b = orientations[(round_index + pair_index) % 4]
            rules.append((tails[left][a], tails[right][b]))
    return tuple(rules)


def _ranking(
    effect: Fraction, support: int, total: int, valid: int, robustness: Robustness, policy: Policy
) -> Ranking:
    return Ranking(
        min(Fraction(1), abs(effect)),
        support,
        min(Fraction(1), Fraction(support, policy.support_target)),
        Fraction(valid, total) if total else Fraction(0),
        robustness,
    )


def _status(ranking: Ranking, policy: Policy) -> Status:
    return (
        "displayable"
        if (
            ranking.support_count >= policy.support_target
            and ranking.completeness == 1
            and ranking.robustness.evaluated == ranking.robustness.planned
            and ranking.robustness.value == 1
        )
        else "interpret_cautiously"
    )


def discover(
    dataset: Dataset,
    *,
    batch_id: str,
    snapshot_id: str,
    student_ids: tuple[str, ...] | None = None,
    policy: Policy = DEFAULT_POLICY,
    boundaries: tuple[BoundaryRequest, ...] = (),
) -> DiscoveryResult:
    require_id(snapshot_id)
    scope, records = _scope(dataset, batch_id, student_ids, policy)
    if len(boundaries) > 20:
        raise AnalysisInputError("boundary_budget_exceeded")
    context = Context(batch_id, snapshot_id, scope)
    summary = summarize(dataset, batch_id=batch_id, student_ids=scope.student_ids)
    correlations = spearman_correlations(
        dataset,
        batch_id=batch_id,
        student_ids=scope.student_ids,
        minimum_pairs=policy.minimum_samples,
    )
    # Hash only normalized values/states, never raw invalid text or comments.
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "version": VERSION,
                "snapshot": snapshot_id,
                "batch": batch_id,
                "policy": asdict(policy),
                "records": [
                    (asdict(r.reference), [(s.state.value, s.value) for s in r.scores])
                    for r in records
                ],
            },
            default=str,
            sort_keys=True,
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    findings = []

    def emit(
        kind: DiscoveryType,
        descriptor: str | int,
        title: str,
        text: str,
        payload: Payload,
        ranking: Ranking,
        source_sets: Sequence[SourceSet],
        limits: Sequence[str] = (),
    ) -> None:
        identity = hashlib.sha256(f"{fingerprint}:{kind}:{descriptor}".encode()).hexdigest()[:24]
        findings.append(
            Discovery(
                identity,
                kind,
                context,
                title,
                text,
                payload,
                ranking,
                "interpret_cautiously"
                if kind in ("rare_combination", "boundary_sensitivity")
                else _status(ranking, policy),
                tuple(source_sets),
                COMMON_LIMITS + tuple(limits),
                policy,
            )
        )

    # Fixed deletion folds are diagnostics, not confidence intervals or random samples.
    folds = tuple(
        tuple(r.reference.student_id for i, r in enumerate(records) if i % 3 != f) for f in range(3)
    )
    folded_pairs = tuple(
        spearman_correlations(
            dataset,
            batch_id=batch_id,
            student_ids=ids,
            minimum_pairs=policy.minimum_samples,
        ).pairs
        for ids in folds
    )
    for index, pair in enumerate(correlations.pairs):
        rho = pair.coefficient
        if rho is None or abs(rho) < policy.correlation_threshold:
            continue
        evaluated = [
            fold[index].coefficient for fold in folded_pairs if fold[index].coefficient is not None
        ]
        robust = Robustness(
            "three_fixed_deletions",
            3,
            len(evaluated),
            sum(
                value * rho > 0 and abs(value) >= policy.correlation_threshold
                for value in evaluated
            ),
        )
        refs = tuple(o.reference for o in pair.observations)
        rank = _ranking(
            Fraction(str(abs(rho))),
            pair.paired_count,
            scope.student_count,
            pair.paired_count,
            robust,
            policy,
        )
        emit(
            "dimension_relation",
            index,
            f"{pair.left.value}与{pair.right.value}的评分关系",
            f"成对有效记录{pair.paired_count}条，排除{len(pair.excluded_records)}条；"
            f"Spearman={rho:.6g}，相关不代表因果。",
            pair,
            rank,
            [SourceSet("paired_scores", (pair.left, pair.right), refs)],
            ["并列评分使用平均秩；删组检查受规范学生顺序影响。"],
        )

    audit = []
    seen = set()
    for conditions in _rules(summary)[: policy.search_budget]:
        comparison = compare_groups(
            dataset,
            batch_id=batch_id,
            student_ids=scope.student_ids,
            conditions=conditions,
            policy=policy,
        )
        target = comparison.selection.matches
        rest = comparison.remainder
        eligible = [
            d
            for d in comparison.dimensions
            if not d.condition_defined
            and min(d.target.valid_count, d.remainder.valid_count) >= policy.minimum_samples
            and d.difference is not None
            and abs(d.difference) >= policy.mean_difference
        ]
        if not eligible:
            audit.append(SearchAttempt(conditions, "insufficient_support_or_effect"))
            continue
        target_set, rest_set = set(target), set(rest)
        partition = frozenset((frozenset(target), frozenset(rest)))
        filter_dimensions = frozenset(c.dimension for c in conditions)
        source_sets = [
            SourceSet("target_members", tuple(c.dimension for c in conditions), target),
            SourceSet("remainder_members", tuple(c.dimension for c in conditions), rest),
        ]
        for d in comparison.dimensions:
            for group, stats in (("target", d.target), ("remainder", d.remainder)):
                key = f"{group}.{d.dimension.name}"
                source_sets.append(SourceSet(key, (d.dimension,), stats.valid_records))
        emitted = False
        for outcome in eligible:
            # Complementary orientations represent the same comparison, but a different
            # filter or outcome dimension changes its meaning even for identical members.
            identity = (partition, filter_dimensions, outcome.dimension)
            if identity in seen:
                continue
            evaluated = passed = 0
            for ids in folds:
                allowed = set(ids)
                a = dimension_stats(
                    tuple(
                        r
                        for r in records
                        if r.reference in target_set and r.reference.student_id in allowed
                    ),
                    outcome.dimension,
                )
                b = dimension_stats(
                    tuple(
                        r
                        for r in records
                        if r.reference in rest_set and r.reference.student_id in allowed
                    ),
                    outcome.dimension,
                )
                if min(a.valid_count, b.valid_count) >= policy.minimum_samples:
                    evaluated += 1
                    delta = a.mean - b.mean
                    passed += (
                        delta * outcome.difference > 0 and abs(delta) >= policy.mean_difference
                    )
            rank = _ranking(
                abs(outcome.difference) / 4,
                min(outcome.target.valid_count, outcome.remainder.valid_count),
                scope.student_count,
                outcome.target.valid_count + outcome.remainder.valid_count,
                Robustness("three_fixed_deletions", 3, evaluated, passed),
                policy,
            )
            emit(
                "group_difference",
                f"{conditions}:{outcome.dimension.name}",
                f"互斥群体的{outcome.dimension.value}评分差异",
                f"目标组{len(target)}人，余组{len(rest)}人；"
                f"{outcome.dimension.value}有效均值差为{outcome.difference}分（目标减余组）。",
                replace(comparison, outcome_dimension=outcome.dimension),
                rank,
                source_sets,
                ["余组排除条件不可判定者；筛选维度差异属于条件定义，不作为新发现。"],
            )
            # Only a published, qualified outcome can reserve a semantic deduplication key.
            seen.add(identity)
            emitted = True
        # One audit entry per attempted rule, including rejected and duplicate attempts.
        audit.append(SearchAttempt(conditions, "qualified" if emitted else "duplicate_partition"))

    for pair in correlations.pairs:
        values = [o.left_score - o.right_score for o in pair.observations]
        if len(values) < policy.minimum_samples:
            continue
        q1, q3 = _quantile(values, Fraction(1, 4)), _quantile(values, Fraction(3, 4))
        center, iqr = median(values), q3 - q1
        lower, upper = q1 - Fraction(3, 2) * iqr, q3 + Fraction(3, 2) * iqr
        for side in ("lower", "upper"):
            observations = tuple(
                o
                for o in pair.observations
                if (
                    (
                        o.left_score - o.right_score < lower
                        if side == "lower"
                        else o.left_score - o.right_score > upper
                    )
                    and abs(o.left_score - o.right_score - center) >= policy.minimum_contrast
                )
            )
            if not observations or Fraction(len(observations), len(values)) > policy.rare_share:
                continue
            outer = sum(
                (
                    o.left_score - o.right_score < q1 - 3 * iqr
                    if side == "lower"
                    else o.left_score - o.right_score > q3 + 3 * iqr
                )
                for o in observations
            )
            refs = tuple(o.reference for o in observations)
            rare = RareCombination(
                pair.left,
                pair.right,
                len(values),
                len(pair.excluded_records),
                q1,
                q3,
                center,
                lower,
                upper,
                side,
                "individual_review"
                if len(refs) == 1
                else "small_group_review"
                if len(refs) < policy.minimum_samples
                else "group_pattern",
                tuple(sorted(Counter(values).items())),
                refs,
                tuple(o.reference for o in pair.observations),
            )
            rank = _ranking(
                max(abs(o.left_score - o.right_score - center) for o in observations) / 8,
                len(refs),
                scope.student_count,
                len(values),
                Robustness("three_iqr_outer_fence", len(refs), len(refs), outer),
                policy,
            )
            emit(
                "rare_combination",
                f"{pair.left.name}:{pair.right.name}:{side}",
                "少见评分组合，待教师复核",
                f"{pair.left.value}减{pair.right.value}的评分差：{len(refs)}/{len(values)}条"
                "位于探索围栏之外，不代表能力缺陷或评分错误。",
                rare,
                rank,
                [
                    SourceSet("rare_members", (pair.left, pair.right), refs),
                    SourceSet(
                        "pair_reference",
                        (pair.left, pair.right),
                        tuple(o.reference for o in pair.observations),
                    ),
                ],
                [
                    "差值围栏是探索启发式，不同维度分差不等价于能力差。",
                    "IQR 为零时最小分差约束仍生效。",
                ],
            )

    for request in boundaries:
        result = compare_boundary(
            dataset,
            batch_id=batch_id,
            student_ids=scope.student_ids,
            request=request,
            policy=policy,
        )
        if not result.observed_boundary or not result.added:
            continue
        eligible = result.strict.eligible_count
        rank = _ranking(
            Fraction(len(result.added), eligible),
            eligible,
            scope.student_count,
            eligible,
            Robustness("exact_boundary_membership", 1, 1, 1),
            policy,
        )
        dims = tuple(c.dimension for c in request.conditions)
        emit(
            "boundary_sensitivity",
            str(request),
            "筛选边界的成员变化",
            f"同一边界从 < 改为 ≤，命中人数由{len(result.strict.matches)}变为"
            f"{len(result.inclusive.matches)}，新增{len(result.added)}人；不改变任何分数。",
            result,
            rank,
            [
                SourceSet("strict_members", dims, result.strict.matches),
                SourceSet("inclusive_members", dims, result.inclusive.matches),
                SourceSet("added_members", dims, result.added),
            ],
            ["边界变化是筛选定义的机械结果，不是新增教学诊断。"],
        )
    findings.sort(key=lambda d: (-d.ranking.score, d.type, d.discovery_id))
    return DiscoveryResult(
        context,
        tuple(findings[: policy.maximum_findings]),
        correlations,
        tuple(audit),
        tuple(
            Quality(d.dimension, d.valid_count, d.missing_count, d.invalid_count)
            for d in summary.dimensions
        ),
        policy,
        len(findings),
    )
