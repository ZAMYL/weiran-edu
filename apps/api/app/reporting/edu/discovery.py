"""Whitelisted presentation of server-recomputed, teacher-confirmed discoveries."""

from fractions import Fraction

from app.edu.discovery_models import (
    BoundaryMetrics,
    DiscoveryDetail,
    DiscoveryExact,
    GroupMetrics,
    GroupPoint,
    RareMetrics,
    RelationMetrics,
)

from .models import ReportInput
from .validation import require, source

LIMITATIONS = frozenset(
    {
        "探索结果不是因果结论或教学效果证明；排序分数不是统计置信度。",
        "多重探索与评分/缺失偏差未经统计检验校正，需教师核查。",
        "仅限获授权内部使用；小样本不宜公开展示，内部引用不是权限凭证。",
        "并列评分使用平均秩；删组检查受规范学生顺序影响。",
        "余组排除条件不可判定者；筛选维度差异属于条件定义，不作为新发现。",
        "差值围栏是探索启发式，不同维度分差不等价于能力差。",
        "IQR 为零时最小分差约束仍生效。",
        "边界变化是筛选定义的机械结果，不是新增教学诊断。",
    }
)


def evidence_notes(finding: DiscoveryDetail) -> tuple[str, tuple[str, ...]]:
    """Keep system evidence separate from teacher judgment; only reviewed fixed prose."""
    require(
        all(text in LIMITATIONS for text in finding.limitations), "unsupported_discovery_limitation"
    )
    status = {
        "displayable": "按当前规则可展示",
        "interpret_cautiously": "需谨慎解释",
        "insufficient_evidence": "证据不足",
    }[finding.status]
    notes = []
    if finding.ranking.support_count < finding.parameters.support_target:
        notes.append("样本支持有限，请结合评分证据复核。")
    stability = finding.ranking.robustness
    if stability.method == "three_fixed_deletions":
        if stability.evaluated < stability.planned:
            notes.append(
                f"稳定性检查未完成：已完成 {stability.evaluated}/{stability.planned} 次；"
                "教师确认不代表稳定性已经验证。"
            )
        elif stability.passed < stability.evaluated:
            notes.append(
                f"稳定性检查未全部通过：{stability.passed}/{stability.evaluated} 次通过；"
                "结果需谨慎解释。"
            )
    return status, (*notes, *finding.limitations)


def exact(value: DiscoveryExact | None) -> Fraction | None:
    return Fraction(value.numerator, value.denominator) if value is not None else None


def validate_discoveries(report: ReportInput) -> None:
    require(
        isinstance(report.discoveries, tuple) and len(report.discoveries) <= 3,
        "discovery_report_limit",
    )
    require(report.reviewed is not None, "saved_discovery_review_required")
    require(
        len({f.discovery_id for f in report.discoveries}) == len(report.discoveries),
        "duplicate_discovery",
    )
    scope_count = report.groups[0].matched_count if report.groups else report.record_count
    for finding in report.discoveries:
        require(type(finding) is DiscoveryDetail, "typed_discovery_required")
        evidence_notes(finding)
        source(type(report.source)(finding.batch_id, finding.snapshot_id), report.source)
        require(
            finding == DiscoveryDetail.model_validate_json(finding.model_dump_json()),
            "invalid_discovery",
        )
        require(
            finding.scope.batch_id == report.source.batch_id
            and finding.scope.snapshot_id == report.source.snapshot_id
            and finding.input_key == finding.scope.input_key
            and finding.scope.batch_record_count == report.record_count
            and finding.scope.scope_record_count == scope_count
            and finding.metrics.student_denominator == scope_count,
            "discovery_scope_mismatch",
        )
        require(
            all(q.discovery_id == finding.discovery_id for q in finding.controlled_queries),
            "discovery_source_mismatch",
        )


def rows(finding: DiscoveryDetail, label) -> tuple[str, tuple[tuple[str, object, str], ...]]:
    """Generate Chinese prose from typed facts, never copied model/user descriptions."""
    metric = finding.metrics
    values: list[tuple[str, object, str]] = [("分析范围人数", metric.student_denominator, "人")]
    if isinstance(metric, RelationMetrics):
        title = f"「{label(metric.left)}」与「{label(metric.right)}」的评分关联"
        values.extend(
            [
                ("有效成对人数", metric.paired_count, "人"),
                (
                    "等级相关系数",
                    round(metric.rho.value, 2) if metric.rho else "无法计算",
                    "不代表因果关系",
                ),
            ]
        )
        if metric.excluded_count:
            values.append(("未参与配对人数", metric.excluded_count, "人"))
    elif isinstance(metric, GroupMetrics):
        title = f"{label(metric.outcome_dimension)}的群体评分差异"
        values.extend(
            [
                ("符合条件人数", metric.target_count, "人"),
                ("其余可判定人数", metric.remainder_count, "人"),
            ]
        )
        if metric.unevaluable_count:
            values.append(("不可判定人数", metric.unevaluable_count, "人"))
        for point in finding.chart:
            if isinstance(point, GroupPoint) and point.dimension_code == metric.outcome_dimension:
                values.extend(
                    [
                        (
                            "所选群体平均分",
                            exact(point.target.mean),
                            "有效评分人数 " + str(point.target.valid_count),
                        ),
                        (
                            "其余群体平均分",
                            exact(point.remainder.mean),
                            "有效评分人数 " + str(point.remainder.valid_count),
                        ),
                        ("两组平均分之差", exact(point.difference), "分；不代表教学效果"),
                    ]
                )
    elif isinstance(metric, RareMetrics):
        title = f"「{label(metric.left)}」与「{label(metric.right)}」的评分组合复核"
        values.extend(
            [
                ("符合复核条件人数", metric.match_count, "人"),
                ("有效成对人数", metric.pair_count, "人"),
                ("占有效成对人数比例", exact(metric.share_pairs), "比例"),
                ("评分差第一四分位数", exact(metric.q1), "分"),
                ("评分差第三四分位数", exact(metric.q3), "分"),
                (
                    "复核边界",
                    exact(metric.lower_fence if metric.side == "lower" else metric.upper_fence),
                    "探索边界，不是能力等级",
                ),
            ]
        )
    else:
        assert isinstance(metric, BoundaryMetrics)
        title = "筛选边界对群体人数的影响"
        values.extend(
            [
                ("严格低于阈值人数", metric.strict_count, "人"),
                ("包含阈值人数", metric.inclusive_count, "人"),
                ("因纳入阈值增加人数", metric.added_count, "人"),
            ]
        )
        if metric.unevaluable_count:
            values.append(("不可判定人数", metric.unevaluable_count, "人"))
    return title, tuple(values)
