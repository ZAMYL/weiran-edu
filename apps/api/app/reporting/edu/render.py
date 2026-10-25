"""Value-only XLSX and escaped Markdown. No I/O, formulas, links, or model calls."""

import html
import re
from datetime import UTC, datetime
from fractions import Fraction
from io import BytesIO
from math import ceil
from unicodedata import east_asian_width
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .layout import (
    NOTES,
    REVIEW_NOTICE,
    condition_text,
    display,
    finding_title,
    label,
    metric_label,
    number_text,
    plan,
    rational,
)
from .models import DetailAuthorization, ExportLimits, ReportError, ReportFiles, ReportInput
from .validation import require, validate


def text_cell(cell, value: str) -> None:
    """Defense in depth even if future approved labels contain spreadsheet syntax."""
    require(
        len(value) <= 1024 and not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value),
        "unsafe_export_text",
    )
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ReportError("unsafe_export_text") from None
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    cell.value = value
    cell.data_type = "s"
    cell.hyperlink = None
    cell.quotePrefix = value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(
        ("\t", "\n", "\r")
    )


def _height(values, widths) -> float:
    lines = 1
    for value, width in zip(values, widths, strict=False):
        if isinstance(value, str):
            effective = sum(2 if east_asian_width(c) in "WF" else 1 for c in value)
            lines = max(lines, ceil(effective / max(1, width - 5)))
    return min(144, max(24, lines * 16 + 8))


def _canonical_zip(raw: bytes, generated_at: datetime) -> bytes:
    result = BytesIO()
    with (
        ZipFile(BytesIO(raw)) as original,
        ZipFile(result, "w", compression=ZIP_DEFLATED) as target,
    ):
        for name in sorted(original.namelist()):
            data = original.read(name)
            if name == "docProps/core.xml":
                root = ElementTree.fromstring(data)
                for field in ("created", "modified"):
                    element = root.find("{http://purl.org/dc/terms/}" + field)
                    if element is not None:
                        element.text = (
                            generated_at.astimezone(UTC).replace(tzinfo=None).isoformat() + "Z"
                        )
                data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            target.writestr(info, data)
    return result.getvalue()


def _xlsx(report: ReportInput, sheets) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.creator = "未然 EDU"
    workbook.properties.title = "教师内部学情分析报告"
    workbook.properties.created = report.generated_at.astimezone(UTC).replace(tzinfo=None)
    for sheet in sheets:
        ws = workbook.create_sheet(sheet.name)
        ws.sheet_view.showGridLines = False
        ws.freeze_panes = "B5"
        text_cell(ws["A2"], sheet.name)
        ws["A2"].font = Font(name="Microsoft YaHei", size=16, bold=True, color="243746")
        ws.row_dimensions[2].height = 28
        for index, width in enumerate(sheet.widths, 1):
            ws.column_dimensions[get_column_letter(index)].width = width
            ws.cell(3, index).border = Border(bottom=Side(style="thin", color="A9B5BE"))
        for number, row in enumerate(sheet.rows, 4):
            for index, item in enumerate(row.cells, 1):
                cell = ws.cell(number, index)
                value = item.value
                if isinstance(value, str):
                    text_cell(cell, value)
                else:
                    cell.value = float(value) if isinstance(value, Fraction) else value
                cell.number_format = item.format
                cell.font = Font(
                    name="Microsoft YaHei",
                    size=11,
                    color="FFFFFF" if row.header else "243746",
                    bold=row.header,
                )
                cell.alignment = Alignment(
                    vertical="center", wrap_text=True, horizontal="center" if row.header else None
                )
                if row.header:
                    cell.fill = PatternFill("solid", fgColor="344653")
                elif number % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F2F5F7")
            ws.row_dimensions[number].height = _height(
                tuple(c.value for c in row.cells),
                sheet.widths,
            )
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.print_area = f"A1:{get_column_letter(len(sheet.widths))}{ws.max_row}"
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return _canonical_zip(stream.getvalue(), report.generated_at)


def md_text(value: object) -> str:
    text = html.escape(str(value), quote=True).replace("\r", "\\r").replace("\n", "\\n")
    for symbol in ("\\", "|", "*", "_", "[", "]", "`"):
        text = text.replace(symbol, "\\" + symbol)
    return text


def _markdown(report: ReportInput, include_details: bool) -> str:
    lines = [
        "# 未然 EDU 学情分析报告" + ("（合成演示）" if report.synthetic else ""),
        "",
        "**教师内部使用，非公开。**",
        "",
        f"批次：{md_text(report.source.batch_id)}  ",
        f"快照：{md_text(report.source.snapshot_id)}  ",
        f"生成时间（UTC）：{report.generated_at.astimezone(UTC).isoformat()}  ",
        f"范围：{'完整批次' if report.scope_kind == 'batch' else '已授权子范围'}，"
        f"测评记录 {report.record_count} 条，统计项目 {len(report.dimensions)} 项。  ",
        f"统计规则：{md_text(report.rule_version)}",
        "",
        "## 维度统计",
        "",
        "| 维度 | 有效/总数 | 缺失/无效 | 均值（展示；精确） | 中位数（展示；精确） | 最小/最大 |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for item in sorted(report.dimensions, key=lambda d: d.code):
        lines.append(
            f"| {md_text(label(item.code))} | {item.valid_count}/{report.record_count} | "
            f"{item.missing_count}/{item.invalid_count} | "
            f"{display(item.mean)}；{rational(item.mean)} | "
            f"{display(item.median)}；{rational(item.median)} | "
            f"{rational(item.minimum)} / {rational(item.maximum)} |"
        )
    if not report.dimensions:
        lines += ["", "未提供统计项目，不以零分代替。"]
    for item in sorted(report.dimensions, key=lambda d: d.code):
        scale = item.scale
        policy = (
            "未提供评分标准和依据版本"
            if not scale
            else f"范围 {number_text(scale.minimum)}—{number_text(scale.maximum)}，"
            f"步长 {number_text(scale.step)}，依据版本 {md_text(scale.rubric_version or '未提供')}"
        )
        bins = "；".join(
            f"{rational(cell.score)}分：{cell.count}/{item.valid_count}"
            f"（{display(Fraction(cell.count, item.valid_count), percent=True)}）"
            for cell in sorted(item.distribution, key=lambda f: f.score)
        )
        lines += [
            "",
            f"**{md_text(label(item.code))}**：{policy}。分布：{bins or '暂无有效数据'}。",
        ]
    lines += ["", "## 群体筛选", ""]
    if not report.groups:
        lines.append("未选择群体；未自动添加默认筛选条件。")
    for group in sorted(report.groups, key=lambda g: g.group_id):
        lines += [
            f"### {md_text(group.group_id)}",
            "",
            f"条件：{md_text(condition_text(group))}",
            f"命中 {group.matched_count}/{group.total_count}，"
            f"比例 {display(group.ratio, percent=True)}（精确 {rational(group.ratio)}）；"
            f"可判定 {group.eligible_count}，不可判定 {group.unevaluable_count}；"
            f"M/E={rational(group.eligible_ratio)}。",
        ]
        if not group.matched_count:
            lines.append("没有匹配记录。")
        if include_details:
            lines += [
                "",
                "| 匿名学生 ID | 维度 | 评分（精确） | 证据 ID |",
                "| --- | --- | ---: | --- |",
            ]
            for row in sorted(group.members, key=lambda r: r.student_id):
                for cell in sorted(row.scores, key=lambda c: c.dimension_code):
                    lines.append(
                        f"| {md_text(row.student_id)} | {md_text(label(cell.dimension_code))} | "
                        f"{rational(cell.value)} | {md_text(cell.evidence_id)} |"
                    )
        else:
            lines.append("默认仅汇总，未导出个人明细。")
        if group.statistics is not None:
            lines += ["", "群体维度统计（分母为该群体有效人数）："]
            lines.extend(
                f"- {md_text(label(d.code))}：有效{d.valid_count}/{group.matched_count}，"
                f"均值{display(d.mean)}（精确{rational(d.mean)}），"
                f"中位数{display(d.median)}（精确{rational(d.median)}）。"
                for d in sorted(group.statistics, key=lambda d: d.code)
            )
        else:
            lines.append("未提供群体维度统计，不能当作零分。")
    lines += [
        "",
        "## 统计事实与待核查事项",
        "",
        "以上数值通过输入一致性检查。"
        + ("洞察卡未提供，" if report.reviewed is None else "")
        + "未执行评语复核；统计与群体表即本报告的事实依据，不另生成教学诊断。",
        "",
    ]
    if report.reviewed is not None:
        reviewed = report.reviewed
        lines += [
            "## 教师核验发现",
            "",
            f"保存结果：{md_text(reviewed.result_id)}；教师核验版本：{reviewed.result_version}；"
            f"报告适配版本：{reviewed.projection_version}。",
            "",
            f"分析条件：{md_text(condition_text(reviewed) or '无群体条件')}。"
            "维度分布对应完整批次；组合群体对应上述条件。",
            "",
            REVIEW_NOTICE,
            "",
        ]
        if not reviewed.findings:
            lines += ["暂无教师已确认发现。pending/dismissed 不作为已确认结论。", ""]
        for finding in reviewed.findings:
            lines += [
                f"### {finding_title(finding)}（教师已确认）",
                "",
                f"候选引用：{md_text(finding.candidate_id)}；来源为确定性系统候选，"
                f"状态 {finding.status}；范围分母 N={finding.denominator}。",
                f"规则：{md_text(finding.rule_id)} / {md_text(finding.rule_version)}。",
                "",
                "| 指标 | 展示值 | 精确值 | 分母 | 实际参与来源记录数 |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
            for metric in finding.metrics:
                value = (
                    str(int(metric.value))
                    if metric.unit == "records"
                    else display(metric.value, percent=metric.unit == "ratio")
                )
                lines.append(
                    f"| {metric_label(metric)} | {value} | {rational(metric.value)} | "
                    f"{metric.denominator} | {metric.source_record_count} |"
                )
            lines += ["", "来源记录数只描述该指标的参与记录，不额外推断群体人数。", ""]
    lines.extend("- " + note for note in NOTES)
    return "\n".join(lines) + "\n"


def generate_report(
    report: ReportInput,
    *,
    include_details: bool = False,
    authorization: DetailAuthorization | None = None,
    limits: ExportLimits | None = None,
) -> ReportFiles:
    """Atomic in-memory pair. Never authenticates, writes paths, or returns partial output."""
    limits = ExportLimits() if limits is None else limits
    validate(report, limits, include_details, authorization)
    sheets = plan(report, include_details, limits)
    markdown = _markdown(report, include_details)
    require(len(markdown.encode("utf-8")) <= limits.max_file_bytes, "report_file_size_limit")
    xlsx = _xlsx(report, sheets)
    require(len(xlsx) <= limits.max_file_bytes, "report_file_size_limit")
    return ReportFiles(xlsx, markdown)
