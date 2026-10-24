"""Deterministic projection of existing analyses; no model, HTTP, file or database access."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable

from .. import select_students, summarize
from ..comments import CommentInput, review_comments
from ..comments.models import Finding, FindingKind
from ..comments.rules import DEFAULT_RULES, RuleSet
from ..engine import cohort
from ..models import (
    AnalysisInputError,
    Condition,
    Dataset,
    Dimension,
    EvidenceKind,
    RecordRef,
    Scope,
    ScoreState,
    ValidatedRecord,
    require_id,
)
from .models import (
    ADAPTER_VERSION,
    DiscoveryPolicy,
    DistributionFocus,
    EvidenceLink,
    GroupRule,
    InsightCandidate,
    Metric,
    QueryRef,
)

LIMITATIONS = (
    "观察来自当前输入快照，不代表学生能力诊断或因果结论。",
    "引用只作内部定位，来源真实性及详情权限须由后端复核。",
    "当前人数是有测评记录的群体，不代表应测花名册人数。",
)


def _canonical_conditions(conditions: Iterable[Condition]) -> tuple[Condition, ...]:
    return tuple(
        sorted(
            conditions,
            key=lambda item: (
                list(Dimension).index(item.dimension),
                item.operator.value,
                item.threshold,
            ),
        )
    )


def _identity(scope: Scope, snapshot_id: str, rule_id: str, descriptor: object) -> str:
    payload = {
        "snapshot": snapshot_id,
        "batch": scope.batch_id,
        "rule": rule_id,
        "version": ADAPTER_VERSION,
        "descriptor": descriptor,
        "scope": [(item.student_id, item.record_id, item.source_id) for item in scope.records],
        "policy": [
            str(scope.policy.minimum),
            str(scope.policy.maximum),
            str(scope.policy.step),
            sorted(scope.policy.missing_tokens),
        ],
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return "candidate-" + digest[:24]


def _restrictions(
    scope: Scope,
    counts: Iterable[tuple[int, int]],
    policy: DiscoveryPolicy,
    *,
    review: bool = False,
) -> tuple[str, ...]:
    flags = []
    if scope.student_count < policy.minimum_public_count:
        flags.append("small_scope")
    if set(scope.student_ids) & policy.sensitive_student_ids:
        flags.append("sensitive_scope")
    for count, denominator in counts:
        if any(0 < value < policy.minimum_public_count for value in (count, denominator - count)):
            flags.append("small_cell_or_complement")
            break
    if review:
        flags.append("individual_evaluation_review")
    return tuple(flags)


def _links(records: Iterable[RecordRef], dimension: Dimension | None) -> tuple[EvidenceLink, ...]:
    return tuple(
        EvidenceLink(record, reference)
        for record in records
        for reference in record.evidence_refs
        if reference.kind is EvidenceKind.SCORE and reference.dimension in {None, dimension}
    )


def metric_sources(candidate: InsightCandidate, key: str) -> tuple[RecordRef, ...]:
    """Resolve the exact metric participants, never the whole denominator scope."""
    metric = next((item for item in candidate.metrics if item.key == key), None)
    if metric is None:
        raise AnalysisInputError("unknown_candidate_metric")
    return metric.source_refs


def discover_candidates(
    dataset: Dataset,
    *,
    batch_id: str,
    snapshot_id: str,
    student_ids: Iterable[str] | None = None,
    groups: Iterable[GroupRule] = (),
    focuses: Iterable[DistributionFocus] = (),
    comments: Iterable[CommentInput] | None = None,
    comment_rules: RuleSet = DEFAULT_RULES,
    policy: DiscoveryPolicy | None = None,
) -> tuple[InsightCandidate, ...]:
    require_id(snapshot_id)
    policy = DiscoveryPolicy() if policy is None else policy
    if not isinstance(policy, DiscoveryPolicy):
        raise AnalysisInputError("discovery_policy_required")
    scope, records = cohort(dataset, batch_id, student_ids)
    group_rules, focus_rules = tuple(groups), tuple(focuses)
    if any(not isinstance(rule, GroupRule) for rule in group_rules) or len(
        {rule.rule_id for rule in group_rules}
    ) != len(group_rules):
        raise AnalysisInputError("unique_typed_group_rules_required")
    if any(not isinstance(rule, DistributionFocus) for rule in focus_rules) or len(
        {rule.condition.dimension for rule in focus_rules}
    ) != len(focus_rules):
        raise AnalysisInputError("one_typed_focus_per_dimension_required")
    output = []
    if policy.include_dimensions and scope.student_count:
        output.extend(
            _dimension_candidates(dataset, scope, records, snapshot_id, focus_rules, policy)
        )
    output.extend(_group_candidates(dataset, scope, snapshot_id, group_rules, policy))
    if comments is not None:
        output.extend(
            _review_candidates(
                dataset,
                comments,
                scope,
                snapshot_id,
                comment_rules,
                policy,
            )
        )
    return tuple(sorted(output, key=lambda item: (item.candidate_type, item.candidate_id)))


def _dimension_candidates(
    dataset: Dataset,
    scope: Scope,
    records: tuple[ValidatedRecord, ...],
    snapshot_id: str,
    focus_rules: tuple[DistributionFocus, ...],
    policy: DiscoveryPolicy,
) -> list[InsightCandidate]:
    batch_id = scope.batch_id
    output = []
    summary = summarize(dataset, batch_id=batch_id, student_ids=scope.student_ids)
    by_dimension = {item.condition.dimension: item for item in focus_rules}
    for stats in summary.dimensions:
        dimension = stats.dimension
        missing = tuple(
            row.reference for row in records if row.score(dimension).state is ScoreState.MISSING
        )
        invalid = tuple(
            row.reference for row in records if row.score(dimension).state is ScoreState.INVALID
        )
        metrics = [
            Metric(
                "valid_count",
                stats.valid_count,
                "records",
                scope.student_count,
                stats.valid_records,
            ),
            Metric("missing_count", stats.missing_count, "records", scope.student_count, missing),
            Metric("invalid_count", stats.invalid_count, "records", scope.student_count, invalid),
            Metric("mean", stats.mean, "score", stats.valid_count, stats.valid_records),
            Metric("median", stats.median, "score", stats.valid_count, stats.valid_records),
        ]
        counts = [
            (stats.valid_count, scope.student_count),
            (stats.missing_count, scope.student_count),
            (stats.invalid_count, scope.student_count),
        ]
        for entry in stats.distribution:
            refs = tuple(
                row.reference
                for row in records
                if row.score(dimension).state is ScoreState.VALID
                and row.score(dimension).value == entry.score
            )
            metrics.append(
                Metric(
                    f"frequency:{entry.score}",
                    entry.share.count,
                    "records",
                    entry.share.denominator,
                    refs,
                    at_score=entry.score,
                )
            )
            counts.append((entry.share.count, entry.share.denominator))
        conditions = ()
        focus = by_dimension.get(dimension)
        title = f"{dimension.value}评分分布"
        text = (
            f"有效{stats.valid_count}/{scope.student_count}条；"
            f"均值{stats.mean if stats.mean is not None else '不可计算'}，"
            f"中位数{stats.median if stats.median is not None else '不可计算'}；"
            f"缺失{stats.missing_count}条、无效{stats.invalid_count}条。"
        )
        if focus:
            selected = select_students(
                dataset,
                batch_id=batch_id,
                student_ids=scope.student_ids,
                conditions=(focus.condition,),
            )
            share = selected.matched_among_eligible
            metrics.extend(
                (
                    Metric(
                        "interval_count",
                        share.count,
                        "records",
                        share.denominator,
                        selected.matches,
                    ),
                    Metric(
                        "interval_ratio",
                        share.proportion,
                        "ratio",
                        share.denominator,
                        selected.matches,
                    ),
                )
            )
            counts.append((share.count, share.denominator))
            conditions = (focus.condition,)
            if share.count and share.proportion >= focus.minimum_share:
                title = f"{dimension.value}在所设评分区间内较集中"
                text += f"所设区间命中{share.count}/{share.denominator}个有效样本。"
        descriptor = [
            dimension.name,
            [(item.operator.value, str(item.threshold)) for item in conditions],
            str(focus.minimum_share) if focus else None,
        ]
        output.append(
            InsightCandidate(
                _identity(scope, snapshot_id, "distribution", descriptor),
                batch_id,
                "dimension_distribution",
                title,
                text,
                tuple(metrics),
                conditions,
                scope.student_count,
                "edu.insight.distribution",
                ADAPTER_VERSION,
                QueryRef("dimension_records", snapshot_id, scope, dimension),
                stats.valid_records,
                _links(stats.valid_records, dimension),
                "observed" if stats.valid_count else "insufficient_data",
                (*LIMITATIONS, "区间条件只用于探索，不是正式教学标准，低分不等于能力不足。"),
                "分别查看有效评分样本及缺失、无效记录，并核对本批评分标准。",
                _restrictions(scope, counts, policy),
            )
        )
    return output


def _group_candidates(
    dataset: Dataset,
    scope: Scope,
    snapshot_id: str,
    group_rules: tuple[GroupRule, ...],
    policy: DiscoveryPolicy,
) -> list[InsightCandidate]:
    batch_id = scope.batch_id
    output = []
    for rule in sorted(group_rules, key=lambda item: item.rule_id):
        conditions = _canonical_conditions(rule.conditions)
        selected = select_students(
            dataset,
            batch_id=batch_id,
            student_ids=scope.student_ids,
            conditions=conditions,
        )
        if not selected.matches:
            continue
        unknown = set(selected.unevaluable_records)
        eligible = tuple(ref for ref in scope.records if ref not in unknown)
        share, complete = selected.matched_share, selected.matched_among_eligible
        metrics = (
            Metric("matched_count", share.count, "records", share.denominator, selected.matches),
            Metric("matched_ratio", share.proportion, "ratio", share.denominator, selected.matches),
            Metric(
                "eligible_count", selected.eligible_count, "records", scope.student_count, eligible
            ),
            Metric(
                "matched_among_eligible",
                complete.proportion,
                "ratio",
                complete.denominator,
                selected.matches,
            ),
            Metric(
                "unevaluable_count",
                len(unknown),
                "records",
                scope.student_count,
                selected.unevaluable_records,
            ),
        )
        label = " 且 ".join(
            f"{item.dimension.value}{item.operator.value}{item.threshold}" for item in conditions
        )
        output.append(
            InsightCandidate(
                _identity(
                    scope,
                    snapshot_id,
                    rule.rule_id,
                    [
                        (item.dimension.name, item.operator.value, str(item.threshold))
                        for item in conditions
                    ],
                ),
                batch_id,
                "ability_group",
                f"能力组合：{label}",
                f"{share.count}/{share.denominator}条记录满足条件，占比{share.proportion}；"
                f"可判定样本{complete.denominator}，不可判定{len(unknown)}条。",
                metrics,
                conditions,
                scope.student_count,
                rule.rule_id,
                ADAPTER_VERSION,
                QueryRef("matched_group_records", snapshot_id, scope),
                selected.matches,
                tuple(
                    dict.fromkeys(
                        link
                        for dimension in Dimension
                        if any(item.dimension is dimension for item in conditions)
                        for link in _links(selected.matches, dimension)
                    )
                ),
                "observed",
                (*LIMITATIONS, "阈值只是探索条件；不同群体可以相交，人数不能直接相加。"),
                "按同一条件与快照查看获授权的匹配记录，再核对各维评分依据。",
                _restrictions(
                    scope,
                    [
                        (share.count, share.denominator),
                        (share.count, complete.denominator),
                        (selected.eligible_count, scope.student_count),
                    ],
                    policy,
                ),
            )
        )
    return output


def _review_candidates(
    dataset: Dataset,
    comments: Iterable[CommentInput],
    scope: Scope,
    snapshot_id: str,
    rules: RuleSet,
    policy: DiscoveryPolicy,
) -> list[InsightCandidate]:
    report = review_comments(
        dataset,
        comments,
        batch_id=scope.batch_id,
        student_ids=scope.student_ids,
        rules=rules,
    )
    buckets: dict[tuple[str, str, Dimension | None], set[Finding]] = defaultdict(set)
    for reviewed in report.records:
        for finding in reviewed.findings:
            if finding.kind is not FindingKind.DUPLICATE:
                buckets[(finding.rule_id, finding.reason_code, finding.dimension)].add(finding)
    output = []
    for duplicate in report.duplicate_groups:
        refs = tuple(dict.fromkeys(comment.assessment for comment in duplicate.comments))
        output.append(
            _review_card(
                scope,
                snapshot_id,
                policy,
                duplicate.rule_id,
                rules,
                "重复评语待复核",
                f"{duplicate.occurrences}条评语涉及{len(refs)}条测评记录；文本重复仅为检查线索。",
                refs,
                tuple(EvidenceLink(item.assessment, item.evidence) for item in duplicate.comments),
                "duplicate_hint",
                (
                    Metric("comment_occurrences", duplicate.occurrences, "comments", None, refs),
                    Metric("involved_records", len(refs), "records", scope.student_count, refs),
                ),
                [item.comment_id for item in duplicate.comments],
            )
        )
    for (rule_id, reason, dimension), findings in sorted(
        buckets.items(),
        key=lambda item: (
            item[0][0],
            item[0][1],
            item[0][2].name if item[0][2] else "",
        ),
    ):
        ordered = sorted(
            findings,
            key=lambda item: (
                tuple(ref.record_id for ref in item.assessments),
                tuple(comment.comment_id for comment in item.comments),
            ),
        )
        refs = tuple(dict.fromkeys(ref for finding in ordered for ref in finding.assessments))
        links = tuple(
            dict.fromkeys(
                [
                    EvidenceLink(comment.assessment, comment.evidence)
                    for finding in ordered
                    for comment in finding.comments
                ]
                + [
                    EvidenceLink(ref, evidence)
                    for finding in ordered
                    for ref in finding.assessments
                    for evidence in finding.score_evidence
                    if evidence in ref.evidence_refs
                ]
            )
        )
        conflict = ordered[0].kind is FindingKind.SCORE_CONFLICT
        title = "评分与文本数值不一致待复核" if conflict else "评价记录待人工核查"
        text = f"{len(refs)}条测评记录待核查。{ordered[0].reason}"
        output.append(
            _review_card(
                scope,
                snapshot_id,
                policy,
                rule_id,
                rules,
                title,
                text,
                refs,
                links,
                "conflict_candidate" if conflict else "needs_manual_review",
                (Metric("involved_records", len(refs), "records", scope.student_count, refs),),
                [reason, dimension.name if dimension else None],
            )
        )
    return output


def _review_card(
    scope: Scope,
    snapshot_id: str,
    policy: DiscoveryPolicy,
    rule_id: str,
    rules: RuleSet,
    title: str,
    summary: str,
    refs: tuple[RecordRef, ...],
    links: tuple[EvidenceLink, ...],
    status: str,
    metrics: tuple[Metric, ...],
    descriptor: object,
) -> InsightCandidate:
    return InsightCandidate(
        _identity(scope, snapshot_id, rule_id, [rules.fingerprint, descriptor]),
        scope.batch_id,
        "evaluation_review",
        title,
        summary,
        metrics,
        (),
        scope.student_count,
        rule_id,
        rules.version,
        QueryRef("review_records", snapshot_id, scope),
        refs,
        links,
        status,
        (*LIMITATIONS, "复核线索不是评价错误结论，不自动修改成绩或评语。"),
        "通过正式授权查询查看关联评分与评语证据，记录人工判断；本模块不回写数据。",
        _restrictions(scope, [(len(refs), scope.student_count)], policy, review=True),
        source_rule_fingerprint=rules.fingerprint,
    )
