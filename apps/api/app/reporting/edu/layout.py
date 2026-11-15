"""Presentation only: teacher sections first, exact values and provenance last."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from fractions import Fraction

from app.importing.edu.types import DIMENSIONS

from .models import ExportLimits, FindingMetric, ReportInput, ReviewedFinding, Scale
from .validation import require

LABELS = {item.code: item.label for item in DIMENSIONS}
OPS = {"lt": "低于", "lte": "不高于", "eq": "等于", "gte": "不低于", "gt": "高于"}
NOTES = (
    "教师内部使用，非公开；匿名标识不等于可以公开传播。",
    "文件可编辑，编辑后不会自动重算平台结果。",
    "统计观察供教学核查使用，不直接代表能力等级或教学效果。",
)
REVIEW_NOTICE = "教师确认只记录核验判断，不证明因果关系、能力等级或实际教学效果。"
METRIC_LABELS = {
    "valid_count": "有效评分人数",
    "missing_count": "缺失评分人数",
    "invalid_count": "无效评分人数",
    "mean": "平均分",
    "median": "中位数",
    "matched_count": "符合条件人数",
    "matched_ratio": "占全部测评人数比例",
    "eligible_count": "可判定人数",
    "matched_among_eligible": "占可判定人数比例",
    "unevaluable_count": "不可判定人数",
}
METRIC_ORDER = {key: index for index, key in enumerate(METRIC_LABELS)}


def label(code: str) -> str:
    return LABELS.get(code, "其他统计项目")


def rational(value: Fraction | None) -> str:
    return "无有效评分" if value is None else f"{value.numerator}/{value.denominator}"


def display(value: Fraction | None, *, percent: bool = False) -> str:
    """Round exact inputs for display only; never use this result in statistics."""
    if value is None:
        return "无有效评分"
    if percent:
        value *= 100
    units, remainder = divmod(abs(value.numerator) * 100, value.denominator)
    units += remainder * 2 >= value.denominator
    sign = "-" if value < 0 and units else ""
    return f"{sign}{units // 100}.{units % 100:02d}" + ("%" if percent else "")


def number_text(value: Fraction) -> str:
    denominator, twos, fives = value.denominator, 0, 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    places = max(twos, fives)
    if denominator != 1 or places > 6:
        return "约 " + display(value)
    if not places:
        return str(value.numerator)
    units = abs(value.numerator) * 10**places // value.denominator
    return ("-" if value < 0 else "") + f"{units // 10**places}.{units % 10**places:0{places}d}"


def score_format(scale: Scale | None) -> str:
    if scale is None:
        return "0.00"
    step = number_text(scale.step)
    if step.startswith("约"):
        return "0.00"
    places = len(step.split(".")[1]) if "." in step else 0
    return "0" + ("." + "0" * places if places else "")


def condition_text(group) -> str:
    return "，且".join(
        f"{label(c.dimension_code)}{OPS[c.operator]} {number_text(c.threshold)} 分"
        for c in sorted(group.conditions, key=lambda c: (c.dimension_code, c.operator, c.threshold))
    )


def finding_title(finding: ReviewedFinding) -> str:
    return (
        label(finding.dimension_code) + "评分分布"
        if finding.candidate_type == "dimension_distribution"
        else "所选能力组合群体"
    )


def metric_label(metric: FindingMetric) -> str:
    return (
        "评分人数：" + number_text(metric.at_score) + " 分"
        if metric.at_score is not None
        else METRIC_LABELS[metric.key]
    )


def visible_metric(metric: FindingMetric) -> bool:
    return metric.at_score is None and not (
        metric.key in {"missing_count", "invalid_count", "unevaluable_count"} and metric.value == 0
    )


def discovery_summary(kind: str, metrics: tuple[tuple[str, object, str], ...]) -> str:
    values = {name: value for name, value, _ in metrics}
    if kind == "dimension_relation":
        coefficient = values["等级相关系数"]
        return f"{values['有效成对人数']:,} 人有两项有效评分 · 相关系数 {coefficient}"
    if kind == "group_difference":
        target = values.get("所选群体平均分")
        remainder = values.get("其余群体平均分")
        if target is not None and remainder is not None:
            return f"所选组均分 {display(target)} · 其余组均分 {display(remainder)}"
        return f"两组可判定人数 {values['符合条件人数']:,} / {values['其余可判定人数']:,} 人"
    if kind == "rare_combination":
        return f"{values['符合复核条件人数']:,} / {values['有效成对人数']:,} 人符合复核条件"
    return f"纳入阈值后由 {values['严格低于阈值人数']:,} 人变为 {values['包含阈值人数']:,} 人"


@dataclass(frozen=True)
class Cell:
    value: object
    format: str = "General"


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]
    header: bool = False
    section: bool = False
    span: bool = False
    percentage_bar: bool = False


@dataclass
class Sheet:
    name: str
    widths: tuple[int, ...]
    rows: list[Row]


def conclusion(report: ReportInput) -> tuple[str, tuple[str, ...]]:
    """Deterministic wording only; no new threshold and no judgement about pass/fail."""
    scored = [item for item in report.dimensions if item.mean is not None]
    valid = sum(item.valid_count for item in report.dimensions)
    missing = sum(item.missing_count for item in report.dimensions)
    invalid = sum(item.invalid_count for item in report.dimensions)
    if not scored or valid == 0:
        return (f"本批 {report.record_count} 名学生没有有效评分，无法给出统计结论。", ())
    low = min(scored, key=lambda item: item.mean)
    high = max(scored, key=lambda item: item.mean)
    headline = (
        f"{len(scored)} 个维度中{label(low.code)}均值最低（{display(low.mean)}），"
        f"{label(high.code)}最高（{display(high.mean)}）。"
    )
    points = []
    if len(scored) > 1:
        points.append(
            f"维度间差距最大的是{label(low.code)}与{label(high.code)}，"
            f"相差 {display(high.mean - low.mean)} 分。"
        )
    quality = f"缺失 {missing} 份、非法 {invalid} 份" if missing or invalid else "无缺失或非法评分"
    points.append(f"数据质量：{quality}；统计基于 {valid} 份有效评分。")
    return headline, tuple(points)


def plan(
    report: ReportInput,
    include_details: bool,
    limits: ExportLimits,
    checkpoint: Callable[[], None] | None = None,
    *,
    spreadsheet: bool = False,
) -> tuple[Sheet, ...]:
    info = Sheet("测评概况", (30, 86), [])
    stats = Sheet("维度统计", (30, 18, 18, 18, 18, 18), [])
    groups = Sheet("选定群体", (30, 62, 38), []) if report.groups else None
    findings = report.reviewed.findings if report.reviewed else ()
    facts = Sheet("教师确认发现", (40, 26, 62), []) if findings or report.discoveries else None
    details = Sheet("授权学生明细", (18, 32, 18), []) if include_details and report.groups else None
    audit = Sheet("技术附录", (38, 38, 38, 18, 56), [])
    if spreadsheet:
        stats.widths = (28, 13, 13, 18, 12, 12, 13, 13)
        audit.widths = (29, 40, 33, 19, 48)
        if facts is not None:
            facts.widths = (51, 52, 25)
        if groups is not None:
            groups.widths = (29, 51, 33)
        if details is not None:
            details.widths = (14, 30, 12)
    sheets = tuple(s for s in (info, stats, groups, facts, details, audit) if s is not None)
    used = 0
    reserved = sum(len(sheet.widths) + 1 for sheet in sheets)

    def add(sheet, *values, header=False, section=False, formats=None, span=False, bar=False):
        nonlocal used
        if checkpoint is not None:
            checkpoint()
        formats = formats or {}
        used += max(len(values), len(sheet.widths)) if section or span else len(values)
        require(
            len(sheet.rows) + 5 <= limits.max_rows_per_sheet
            and used + reserved <= limits.max_total_cells,
            "report_layout_limit",
        )
        sheet.rows.append(
            Row(
                tuple(Cell(v, formats.get(i, "General")) for i, v in enumerate(values)),
                header,
                section,
                span,
                bar,
            )
        )

    add(info, "项目", "内容", header=True)
    if report.synthetic:
        add(info, "数据性质", "合成演示，不含真实学生数据")
    add(info, "测评记录数", report.record_count, formats={1: "#,##0"})
    add(info, "统计项目数", len(report.dimensions), formats={1: "#,##0"})
    headline, points = conclusion(report)
    add(info, "本次结论", headline, section=True)
    for point in points:
        add(info, "", point)
    if report.reviewed is not None:
        add(info, "教师已确认发现数", len(findings) + len(report.discoveries), formats={1: "#,##0"})
    if report.reviewed is not None and not findings and not report.discoveries:
        add(info, "未发现显著问题的原因", "本次运行没有达到统计口径阈值的发现。", section=True)
        add(
            info,
            "",
            f"样本 {report.record_count} 名学生；规则版本 {report.rule_version}；"
            "当前为规则判定，未做显著性检验。",
        )
        add(info, "", "阈值与最小群体门槛未调整；调整后可能产生不同结果。")
    add(info, "分析范围", "完整批次" if report.scope_kind == "batch" else "已授权子范围")
    add(
        info,
        "生成时间（协调世界时）",
        report.generated_at.astimezone(UTC).replace(tzinfo=None),
        formats={1: "yyyy-mm-dd hh:mm:ss"},
    )
    add(info, "阅读说明", "均值和中位数仅使用有效评分；缺失和无效评分不按零分计算。")
    for note in NOTES:
        add(info, "使用说明", note)

    def dimension_rows(dims, total):
        quality = ("缺失人数", "无效人数") if spreadsheet else ()
        add(
            stats, "维度", "有效人数", "平均分", "中位数", "最低分", "最高分", *quality, header=True
        )
        for item in sorted(dims, key=lambda d: d.code):
            add(
                stats,
                label(item.code),
                item.valid_count,
                item.mean,
                item.median,
                item.minimum,
                item.maximum,
                *((item.missing_count, item.invalid_count) if spreadsheet else ()),
                formats={
                    1: "#,##0",
                    2: "0.00",
                    3: "0.00",
                    4: "0.00",
                    5: "0.00",
                    6: "#,##0",
                    7: "#,##0",
                },
            )
        affected = [d for d in dims if d.missing_count or d.invalid_count]
        if affected and not spreadsheet:
            add(stats, "评分质量说明", section=True)
            add(stats, "维度", "缺失人数", "无效人数", "影响说明", header=True)
            for item in sorted(affected, key=lambda d: d.code):
                add(
                    stats,
                    label(item.code),
                    item.missing_count,
                    item.invalid_count,
                    f"未参与统计；有效样本 {item.valid_count} 人，共 {total} 人。",
                    formats={1: "#,##0", 2: "#,##0"},
                )
        if any(d.valid_count == 0 for d in dims):
            add(stats, "阅读说明", "无有效评分的统计项留空，不按零分计算。", span=spreadsheet)

    if report.dimensions:
        dimension_rows(report.dimensions, report.record_count)
    else:
        add(stats, "当前范围没有统计项目。", span=spreadsheet)

    if spreadsheet and any(item.valid_count for item in report.dimensions):
        add(stats, "本次测评评分分布", section=True)
        add(stats, "维度", "评分（分）", "人数", "占有效评分比例", header=True)
        for item in sorted(report.dimensions, key=lambda d: d.code):
            for cell in sorted(item.distribution, key=lambda f: f.score):
                add(
                    stats,
                    label(item.code),
                    cell.score,
                    cell.count,
                    Fraction(cell.count, item.valid_count),
                    formats={1: "0.00####", 2: "#,##0", 3: "0.0%"},
                    bar=True,
                )

    sorted_groups = sorted(report.groups, key=lambda g: g.group_id)
    for index, group in enumerate(sorted_groups, 1):
        scope = f"群体 {index}"
        add(groups, scope, section=True)
        add(groups, "项目", "结果", "统计口径", header=True)
        add(groups, "筛选条件", condition_text(group), span=spreadsheet)
        for name, value, denominator, rate in (
            ("符合条件人数", group.matched_count, group.total_count, False),
            ("全部测评人数", group.total_count, group.total_count, False),
            ("占全部测评人数比例", group.ratio, group.total_count, True),
            ("可判定人数", group.eligible_count, group.total_count, False),
            ("占可判定人数比例", group.eligible_ratio, group.eligible_count, True),
        ):
            add(
                groups,
                name,
                value,
                f"以 {denominator} 人为范围" if rate else "人",
                formats={1: "0.00%" if rate else "#,##0"},
            )
        if group.unevaluable_count:
            add(
                groups,
                "不可判定人数",
                group.unevaluable_count,
                "相关评分缺失或无效；仍保留在全部测评人数中。",
                formats={1: "#,##0"},
            )
        if not group.matched_count:
            add(groups, "筛选结果", "没有匹配记录")
        if group.statistics:
            add(stats, scope + " 维度统计", section=True)
            dimension_rows(group.statistics, group.matched_count)
        if group.statistics is None and group.matched_count:
            add(groups, "统计说明", "本次仅提供群体人数，未提供群体维度统计。")
        if details is not None:
            add(details, scope, section=True)
            add(details, "学生序号", "维度", "评分", header=True)
            for number, student in enumerate(sorted(group.members, key=lambda s: s.student_id), 1):
                for cell in sorted(student.scores, key=lambda s: s.dimension_code):
                    scale = next(
                        d.scale for d in report.dimensions if d.code == cell.dimension_code
                    )
                    add(
                        details,
                        number,
                        label(cell.dimension_code),
                        cell.value,
                        formats={0: "#,##0", 2: score_format(scale)},
                    )
        if len(sorted_groups) > 1 and not spreadsheet:
            add(groups, "人数说明", "不同群体可能重叠，人数不能直接相加。")

    if spreadsheet and len(sorted_groups) > 1:
        add(groups, "人数说明", "不同群体可能重叠，人数不能直接相加。", span=True)

    if facts is not None:
        if spreadsheet:
            add(facts, "展开左侧「＋」查看数据与统计口径。 " + REVIEW_NOTICE, span=True)
            add(facts, "发现", "关键结果", "评分依据状态", header=True)
        else:
            add(facts, "核验说明", "", REVIEW_NOTICE)
        for index, finding in enumerate(findings, 1):
            title = f"发现 {index}：{finding_title(finding)}"
            if spreadsheet:
                metrics_by_name = {m.key: m.value for m in finding.metrics if m.at_score is None}
                if finding.candidate_type == "ability_group":
                    key_result = f"符合条件 {int(metrics_by_name['matched_count']):,} 人"
                else:
                    key_result = (
                        f"有效评分 {int(metrics_by_name['valid_count']):,} 人 · "
                        f"平均分 {display(metrics_by_name['mean'])}"
                    )
                status = "评分充足" if finding.status == "observed" else "有效评分不足"
                add(facts, title, key_result, status, section=True)
            else:
                add(facts, title + "（教师已确认）", section=True)
            if finding.candidate_type == "ability_group":
                add(facts, "筛选条件", condition_text(report.reviewed), span=spreadsheet)
            add(facts, "指标", "结果", "统计口径", header=True)
            for metric in sorted(
                filter(visible_metric, finding.metrics), key=lambda m: METRIC_ORDER[m.key]
            ):
                value = int(metric.value) if metric.unit == "records" else metric.value
                add(
                    facts,
                    metric_label(metric),
                    value,
                    f"有效评分 {metric.denominator} 人"
                    if metric.unit == "score"
                    else f"以 {metric.denominator} 人为范围"
                    if metric.unit == "ratio"
                    else "人",
                    formats={
                        1: "#,##0"
                        if metric.unit == "records"
                        else "0.00%"
                        if metric.unit == "ratio"
                        else "0.00"
                    },
                )

    if report.discoveries:
        from .discovery import evidence_notes, exact, rows

        common_notes = ()
        if spreadsheet and len(report.discoveries) > 1:
            first, *others = report.discoveries
            common_notes = tuple(
                note
                for note in first.limitations
                if all(note in finding.limitations for finding in others)
            )
        for index, finding in enumerate(report.discoveries, len(findings) + 1):
            title, metrics = rows(finding, label)
            status, notes = evidence_notes(finding)
            title = f"发现 {index}：{title}"
            text = ""
            if finding.conditions:
                text = "，且".join(
                    f"{label(c.dimension_code)}{OPS[c.operator]} "
                    f"{number_text(exact(c.threshold))} 分"
                    for c in finding.conditions
                )
            if spreadsheet:
                key_result = discovery_summary(finding.type, metrics)
                if text:
                    key_result += "\n" + text
                add(facts, title, key_result, status, section=True)
            else:
                add(facts, title + "（教师已确认）", section=True)
                add(facts, "系统证据状态", status, "与教师核验判断分别保留")
                for note in notes:
                    add(facts, "解释限制", note)
                if text:
                    add(facts, "群体筛选条件", text, "用于本项发现；整体分析范围见概况")
            add(facts, "指标", "结果", "统计口径", header=True)
            for name, value, note in metrics:
                if value is None:
                    value = "无有效评分"
                add(
                    facts,
                    name,
                    value,
                    note,
                    formats={
                        1: "#,##0" if type(value) is int else "0.00%" if note == "比例" else "0.00"
                    },
                )
            if spreadsheet:
                for note in notes:
                    if note not in common_notes:
                        add(facts, "解释限制", note, span=True)
            else:
                add(facts, "使用说明", "仅反映本次测评记录；供教师复核，不作为能力标签。")
        if spreadsheet:
            add(facts, "共同使用限制", section=True, span=True)
            add(facts, "仅反映本次测评记录；供教师复核，不作为能力标签。", span=True)
            for note in common_notes:
                add(facts, note, span=True)

    add(
        audit,
        "核查说明",
        (
            "展开左侧「＋」核查精确值与评分依据。分数形式的分母不代表样本人数。"
            if spreadsheet
            else "本附录保留溯源引用与精确值；有理数的分母不是学生人数。"
        ),
        span=spreadsheet,
    )
    add(
        audit,
        "量表说明" if spreadsheet else "核查说明",
        "不同量表不默认可比；评分范围、步长和版本见下表。",
        span=spreadsheet,
    )
    if spreadsheet:
        add(audit, "来源与版本", "引用或内容", header=True, span=True)
    else:
        add(audit, "项目", "值", header=True)
    for name, value in (
        ("测评批次引用", report.source.batch_id),
        ("数据快照引用", report.source.snapshot_id),
        ("统计规则版本", report.rule_version),
        ("匿名明细", "已按授权导出" if include_details else "默认仅汇总"),
    ):
        add(audit, name, value, span=spreadsheet)
    if report.reviewed is not None:
        add(audit, "保存结果引用", report.reviewed.result_id, span=spreadsheet)
        add(
            audit,
            "教师核验版本",
            report.reviewed.result_version,
            formats={1: "#,##0"},
            span=spreadsheet,
        )
        add(audit, "报告适配版本", report.reviewed.projection_version, span=spreadsheet)

    scopes = [
        (
            "完整批次" if report.scope_kind == "batch" else "已授权子范围",
            report.record_count,
            report.dimensions,
        )
    ]
    for index, group in enumerate(sorted_groups, 1):
        scope = f"群体 {index}"
        add(audit, scope + "筛选核查", section=True)
        add(audit, "范围", "指标或引用", "精确值或规则", "样本人数", "条件说明", header=True)
        add(audit, scope, group.group_id, group.rule_version)
        for condition in group.conditions:
            add(
                audit,
                scope,
                "筛选阈值：" + label(condition.dimension_code),
                rational(condition.threshold),
                None,
                OPS[condition.operator],
            )
        for name, value, denominator in (
            ("符合条件人数", Fraction(group.matched_count), group.total_count),
            ("可判定人数", Fraction(group.eligible_count), group.total_count),
            ("不可判定人数", Fraction(group.unevaluable_count), group.total_count),
            ("占全部测评人数比例", group.ratio, group.total_count),
            ("占可判定人数比例", group.eligible_ratio, group.eligible_count),
        ):
            add(audit, scope, name, rational(value), denominator)
        if group.statistics is not None:
            scopes.append((scope, group.matched_count, group.statistics))
    add(audit, "维度精确统计", section=True)
    add(audit, "范围", "维度及指标", "精确值", "样本人数", "内部项目引用", header=True)
    for scope, total, dims in scopes:
        for item in sorted(dims, key=lambda d: d.code):
            for name, value, denominator in (
                ("有效人数", Fraction(item.valid_count), total),
                ("缺失人数", Fraction(item.missing_count), total),
                ("无效人数", Fraction(item.invalid_count), total),
                ("平均分", item.mean, item.valid_count),
                ("中位数", item.median, item.valid_count),
                ("最低分", item.minimum, item.valid_count),
                ("最高分", item.maximum, item.valid_count),
            ):
                add(
                    audit,
                    scope,
                    label(item.code) + "：" + name,
                    rational(value),
                    denominator,
                    item.code,
                    formats={3: "#,##0"},
                )
    add(audit, "评分标准与分布明细", section=True)
    add(audit, "范围及维度", "项目", "评分或精确值", "人数", "比例或说明", header=True)
    for scope, _, dims in scopes:
        for item in sorted(dims, key=lambda d: d.code):
            reference = scope + "：" + label(item.code)
            if item.scale:
                for name, value in (
                    ("量表下限", item.scale.minimum),
                    ("量表上限", item.scale.maximum),
                    ("步长", item.scale.step),
                ):
                    add(audit, reference, name, rational(value))
                add(audit, reference, "评分依据版本", item.scale.rubric_version or "未提供")
            else:
                add(audit, reference, "量表说明", "未提供评分标准和依据版本，不能推定量表可比。")
            for cell in sorted(item.distribution, key=lambda f: f.score):
                ratio = Fraction(cell.count, item.valid_count)
                add(
                    audit,
                    reference,
                    "评分频数",
                    rational(cell.score),
                    cell.count,
                    ratio,
                    formats={3: "#,##0", 4: "0.00%"},
                )
                add(audit, reference, "频数比例精确值", rational(ratio), item.valid_count)
    if findings:
        add(audit, "已确认发现溯源", section=True)
        add(
            audit, "发现及指标", "精确值或引用", "统计分母", "参与记录数", "规则或说明", header=True
        )
        for index, finding in enumerate(findings, 1):
            ref = f"发现 {index}"
            add(
                audit,
                ref,
                finding.candidate_id,
                finding.denominator,
                None,
                finding.rule_id + " / " + finding.rule_version,
            )
            add(audit, ref, "评分充足" if finding.status == "observed" else "有效评分不足")
            for metric in finding.metrics:
                add(
                    audit,
                    ref + "：" + metric_label(metric),
                    rational(metric.value),
                    metric.denominator,
                    metric.source_record_count,
                    "参与记录数仅说明该指标的来源，不另行推断群体人数。",
                )
    if report.discoveries:
        from .discovery import rows

        add(audit, "已确认主动发现溯源", section=True)
        if spreadsheet:
            add(audit, "发现", "核查项", "引用或精确值", "人数", "规则或口径", header=True)
        for index, finding in enumerate(report.discoveries, 1):
            ref = f"主动发现 {index}"
            if spreadsheet:
                add(
                    audit,
                    ref,
                    "发现引用",
                    finding.discovery_id,
                    None,
                    f"{finding.rule_id} / {finding.rule_version}",
                )
                add(audit, ref, "分析范围引用", finding.input_key)
            else:
                add(audit, ref, finding.discovery_id, finding.rule_id, finding.rule_version)
                add(audit, "分析范围引用", finding.input_key)
            for name, value, note in rows(finding, label)[1]:
                if isinstance(value, Fraction):
                    if spreadsheet:
                        add(audit, ref, name, rational(value), None, note)
                    else:
                        add(audit, name, rational(value), note)
            if finding.type == "dimension_relation" and finding.metrics.rho is not None:
                prefix = (ref,) if spreadsheet else ()
                add(audit, *prefix, "等级相关系数原值", str(finding.metrics.rho.value))
            for query in finding.controlled_queries:
                prefix = (ref,) if spreadsheet else ()
                add(audit, *prefix, "授权证据查询指标", query.metric_key)
    if details is not None:
        add(audit, "授权明细引用", section=True)
        add(
            audit, "群体及学生序号", "匿名学生引用", "维度", "精确评分", "评分证据引用", header=True
        )
        for index, group in enumerate(sorted_groups, 1):
            for number, student in enumerate(sorted(group.members, key=lambda s: s.student_id), 1):
                for cell in sorted(student.scores, key=lambda s: s.dimension_code):
                    add(
                        audit,
                        f"群体 {index} 学生 {number}",
                        student.student_id,
                        label(cell.dimension_code),
                        rational(cell.value),
                        cell.evidence_id,
                    )
    return sheets
