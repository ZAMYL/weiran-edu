"""A bounded, teacher-readable workbook plan built only from validated values."""

from dataclasses import dataclass
from datetime import UTC
from fractions import Fraction

from app.importing.edu.types import DIMENSIONS

from .models import ExportLimits, ReportInput
from .validation import require

LABELS = {item.code: item.label for item in DIMENSIONS}
OPS = {"lt": "<", "lte": "<=", "eq": "=", "gte": ">=", "gt": ">"}
NOTES = (
    "教师内部使用，非公开报告；匿名标识不等于可公开传播。",
    "这是可编辑的统计快照，编辑文件不会自动重算平台结果。",
    "缺失和无效评分未作为零分；均值与分布的样本分母仅为有效人数。",
    "精确值中的有理数分母不是学生人数。不同量表不默认可比。",
    "未输出身份、原始评语、未审核依据原文、内部定位或文件路径。",
    "统计观察不代表能力等级、教学诊断、因果关系或已验证教学效果。",
    "洞察卡未提供，评语分析不可用；未把 withheld 当作空评语或零异常。",
)


def label(code: str) -> str:
    return LABELS.get(code, "统计项目 " + code)


def rational(value: Fraction | None) -> str:
    return "暂无有效数据" if value is None else f"{value.numerator}/{value.denominator}"


def display(value: Fraction | None, *, percent: bool = False) -> str:
    if value is None:
        return "暂无有效数据"
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
        return rational(value)
    if not places:
        return str(value.numerator)
    units = abs(value.numerator) * 10**places // value.denominator
    return ("-" if value < 0 else "") + f"{units // 10**places}.{units % 10**places:0{places}d}"


def score_format(scale) -> str:
    if scale is None:
        return "0.000000"
    step = number_text(scale.step)
    if "/" in step:
        return "0.000000"
    places = len(step.split(".")[1]) if "." in step else 0
    return "0" + ("." + "0" * places if places else "")


def condition_text(group) -> str:
    return " AND ".join(
        f"{label(c.dimension_code)} {OPS[c.operator]} {number_text(c.threshold)}"
        for c in sorted(group.conditions, key=lambda c: (c.dimension_code, c.operator, c.threshold))
    )


@dataclass(frozen=True)
class Cell:
    value: object
    format: str = "General"


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]
    header: bool = False


@dataclass
class Sheet:
    name: str
    widths: tuple[int, ...]
    rows: list[Row]


def plan(report: ReportInput, include_details: bool, limits: ExportLimits) -> tuple[Sheet, ...]:
    sheets = (
        Sheet("报告说明", (25, 52, 70), []),
        Sheet("维度统计", (28, 28, 24, 18, 20, 28, 19, 28), []),
        Sheet("群体结果", (27, 40, 28, 28, 30, 38), []),
        Sheet("洞察事实", (30, 26, 75, 28), []),
    )
    used = 0
    reserved = sum(len(sheet.widths) + 1 for sheet in sheets)

    def add(sheet, *values, header=False, formats=None):
        nonlocal used
        formats = formats or {}
        used += len(values)
        require(
            len(sheet.rows) + 5 <= limits.max_rows_per_sheet
            and used + reserved <= limits.max_total_cells,
            "report_layout_limit",
        )
        sheet.rows.append(
            Row(tuple(Cell(v, formats.get(i, "General")) for i, v in enumerate(values)), header)
        )

    info, stats, groups, facts = sheets
    add(info, "项目", "内容", "说明", header=True)
    for key, value, note in (
        (
            "数据性质",
            "合成演示" if report.synthetic else "调用方提供的统计快照",
            "仅用于已获授权的内部用途",
        ),
        ("测评批次", report.source.batch_id, "来源为调用方提供并校验的统计快照"),
        ("快照", report.source.snapshot_id, "所有统计、群体及引用必须一致"),
        (
            "生成时间（UTC）",
            report.generated_at.astimezone(UTC).replace(tzinfo=None),
            "显式输入，非测评日期",
        ),
        (
            "分析范围",
            "完整批次" if report.scope_kind == "batch" else "已授权子范围",
            "不是应测花名册人数",
        ),
        ("测评记录数", report.record_count, "不因群体或导出分页改变"),
        ("统计项目数", len(report.dimensions), "按实际项目渲染，不代表支持其他上传格式"),
        ("统计规则版本", report.rule_version, "不是教学因果或综合能力等级"),
        (
            "匿名明细",
            "已按调用方授权声明导出" if include_details else "默认仅汇总",
            "导出器本身不负责真实鉴权",
        ),
    ):
        add(info, key, value, note, formats={1: "yyyy-mm-dd hh:mm:ss"} if "时间" in key else {})
    for item in sorted(report.dimensions, key=lambda d: d.code):
        scale = item.scale
        value = (
            "未提供评分标准"
            if scale is None
            else (
                f"范围 {number_text(scale.minimum)}—{number_text(scale.maximum)}，"
                f"步长 {number_text(scale.step)}"
            )
        )
        add(
            info,
            label(item.code),
            value,
            "依据版本：" + (scale.rubric_version or "未提供")
            if scale
            else "不推定范围、步长或量表版本",
        )
    for note in NOTES:
        add(info, "限制与核查", note, "")

    add(
        stats,
        "范围",
        "维度",
        "指标",
        "数值",
        "展示值",
        "精确值",
        "样本分母（人）",
        "状态",
        header=True,
    )
    scopes = [("分析范围", report.record_count, report.dimensions)]
    scopes.extend(
        (g.group_id, g.matched_count, g.statistics)
        for g in report.groups
        if g.statistics is not None
    )
    for scope, total, dims in scopes:
        for item in sorted(dims, key=lambda d: d.code):
            for name, value, denominator in (
                ("有效数", item.valid_count, total),
                ("缺失数", item.missing_count, total),
                ("无效数", item.invalid_count, total),
                ("均值", item.mean, item.valid_count),
                ("中位数", item.median, item.valid_count),
                ("最小值", item.minimum, item.valid_count),
                ("最大值", item.maximum, item.valid_count),
            ):
                integer = type(value) is int
                add(
                    stats,
                    scope,
                    label(item.code),
                    name,
                    value,
                    str(value) if integer else display(value),
                    rational(Fraction(value) if integer else value),
                    denominator,
                    "暂无有效数据" if value is None else "统计观察",
                    formats={3: "#,##0" if integer else "0.0000"},
                )
    if not report.dimensions:
        add(stats, "分析范围", "未提供统计项目", "", None, "", "", report.record_count, "未提供")
    add(stats)
    add(
        stats,
        "范围",
        "维度",
        "评分",
        "精确评分",
        "人数",
        "有效样本数",
        "比例",
        "精确比例",
        header=True,
    )
    for scope, _, dims in scopes:
        for item in sorted(dims, key=lambda d: d.code):
            for cell in sorted(item.distribution, key=lambda f: f.score):
                ratio = Fraction(cell.count, item.valid_count)
                add(
                    stats,
                    scope,
                    label(item.code),
                    cell.score,
                    rational(cell.score),
                    cell.count,
                    item.valid_count,
                    ratio,
                    rational(ratio),
                    formats={2: score_format(item.scale), 4: "#,##0", 5: "#,##0", 6: "0.00%"},
                )

    add(groups, "群体", "项目", "数值", "精确值", "样本分母（人）", "条件或状态", header=True)
    if not report.groups:
        add(groups, "未选择群体", "", None, "", None, "未自动添加默认筛选")
    for group in sorted(report.groups, key=lambda g: g.group_id):
        for name, value, denominator in (
            ("命中人数 M", group.matched_count, group.total_count),
            ("总人数 N", group.total_count, group.total_count),
            ("可判定人数 E", group.eligible_count, group.total_count),
            ("不可判定人数", group.unevaluable_count, group.total_count),
            ("命中比例 M/N", group.ratio, group.total_count),
            ("可判定中比例 M/E", group.eligible_ratio, group.eligible_count),
        ):
            rate = isinstance(value, Fraction) or value is None
            add(
                groups,
                group.group_id,
                name,
                value,
                rational(Fraction(value) if type(value) is int else value),
                denominator,
                condition_text(group),
                formats={2: "0.00%" if rate else "#,##0"},
            )
        state = (
            "没有匹配记录"
            if not group.matched_count
            else "完整匿名清单已导出"
            if include_details
            else "默认仅汇总，未导出个人明细"
        )
        add(groups, group.group_id, "清单状态", None, "", group.total_count, state)
        if group.statistics is None:
            add(groups, group.group_id, "群体维度统计", None, "", None, "未提供，不能当作零分")
    if include_details:
        add(groups)
        add(groups, "群体", "匿名学生 ID", "维度", "评分", "精确评分", "证据 ID", header=True)
        for group in sorted(report.groups, key=lambda g: g.group_id):
            for student in sorted(group.members, key=lambda s: s.student_id):
                for cell in sorted(student.scores, key=lambda s: s.dimension_code):
                    add(
                        groups,
                        group.group_id,
                        student.student_id,
                        label(cell.dimension_code),
                        cell.value,
                        rational(cell.value),
                        cell.evidence_id,
                        formats={
                            3: score_format(
                                next(
                                    d.scale
                                    for d in report.dimensions
                                    if d.code == cell.dimension_code
                                )
                            )
                        },
                    )
    add(facts, "事实引用", "来源", "统计观察", "需核查事项", header=True)
    add(
        facts,
        "insights-not-provided",
        "当前报告输入",
        "洞察卡未提供；未执行评语复核",
        "扣留或缺少原文不代表零异常",
    )
    for item in sorted(report.dimensions, key=lambda d: d.code):
        add(
            facts,
            "dimension." + item.code,
            report.rule_version,
            f"{label(item.code)}有效{item.valid_count}/{report.record_count}，"
            f"均值{display(item.mean)}（精确{rational(item.mean)}），"
            f"缺失{item.missing_count}、无效{item.invalid_count}。",
            "数据与量表由上层核验；统计不是诊断",
        )
    for group in sorted(report.groups, key=lambda g: g.group_id):
        add(
            facts,
            group.group_id,
            group.rule_version,
            f"命中{group.matched_count}/{group.total_count}，"
            f"比例{display(group.ratio, percent=True)}。",
            "没有匹配记录" if not group.matched_count else "群体可能重叠，人数不可直接相加",
        )
    return sheets
