"""Value-only XLSX and escaped Markdown. No I/O, formulas, links, or model calls."""

import html
import re
from collections.abc import Callable
from datetime import UTC, datetime
from fractions import Fraction
from io import BytesIO
from math import ceil
from unicodedata import east_asian_width
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.data_source import AxDataSource, NumData, NumVal, StrData, StrRef, StrVal
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .layout import Cell, display, plan
from .models import DetailAuthorization, ExportLimits, ReportError, ReportFiles, ReportInput
from .validation import require, validate

INK = "182C50"
BLUE = "2463F5"
MUTED = "516580"
RULE = "D8E4F5"
SECTION = "EAF1FF"
STRIPE = "F6F9FE"
FONT = "Microsoft YaHei"
WARNING_FILL = "FFF0D5"
WARNING_INK = "854B0D"
TAB_COLORS = {
    "测评概况": BLUE,
    "维度统计": BLUE,
    "教师确认发现": "148184",
    "选定群体": "148184",
    "授权学生明细": "667895",
    "技术附录": "667895",
}
FREEZE_PANES = {
    "维度统计": "B4",
    "教师确认发现": "A6",
    "选定群体": "A4",
    "授权学生明细": "B4",
    "技术附录": "B4",
}
EVIDENCE_WARNINGS = {"需谨慎解释", "证据不足", "有效评分不足"}


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
            wrapped_lines = (
                max(
                    1,
                    ceil(
                        sum(2 if east_asian_width(c) in "WF" else 1 for c in line)
                        / max(1, width - 5)
                    ),
                )
                for line in value.splitlines()
            )
            lines = max(lines, sum(wrapped_lines))
    return min(144, max(21, lines * 14 + 7))


def _canonical_zip(raw: bytes, generated_at: datetime, checkpoint=None) -> bytes:
    result = BytesIO()
    with (
        ZipFile(BytesIO(raw)) as original,
        ZipFile(result, "w", compression=ZIP_DEFLATED) as target,
    ):
        for name in sorted(original.namelist()):
            if checkpoint is not None:
                checkpoint()
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


def _xlsx(report: ReportInput, sheets, checkpoint=None) -> bytes:
    workbook = Workbook()
    try:
        return _write_xlsx(workbook, report, sheets, checkpoint)
    finally:
        workbook.close()


def _summary_charts(workbook, report: ReportInput) -> None:
    if not report.dimensions or not report.record_count:
        return
    overview, stats = workbook["测评概况"], workbook["维度统计"]
    # The first statistics block lists the full report scope, before any selected groups.
    last_row = 4 + len(report.dimensions)
    names = [stats.cell(row, 1).value for row in range(5, last_row + 1)]
    if len(set(names)) != len(names):
        return
    labels = Reference(stats, min_col=1, min_row=5, max_row=last_row)
    categories = StrData(
        ptCount=len(names), pt=[StrVal(idx=i, v=name) for i, name in enumerate(names)]
    )
    scale = report.dimensions[0].scale
    comparable = scale is not None and all(item.scale == scale for item in report.dimensions)
    charts = []
    if comparable and any(item.valid_count for item in report.dimensions):
        charts.append(
            (
                "各维度平均分与中位数（分）",
                (3, 4),
                min(0, float(scale.minimum)),
                max(0, float(scale.maximum)),
                "0.0#",
            )
        )
    charts.append(("各维度有效评分人数（人）", (2,), 0, report.record_count, "#,##0"))

    height_rows = max(12, len(names) * 2 + 2)
    for index, (title, columns, minimum, maximum, number_format) in enumerate(charts):
        chart = BarChart()
        chart.type = "bar"
        chart.title = title
        chart.y_axis.scaling.min, chart.y_axis.scaling.max = minimum, maximum
        chart.y_axis.numFmt = number_format
        if number_format == "#,##0":
            chart.y_axis.majorUnit = max(1, ceil(maximum / 5))
        chart.x_axis.scaling.orientation = "maxMin"
        chart.y_axis.crosses = "max"
        chart.display_blanks = "gap"
        chart.gapWidth = 75
        if len(columns) == 1:
            chart.legend = None
        else:
            chart.legend.position = "b"
        for column, color in zip(columns, (BLUE, "7E96BB"), strict=False):
            chart.add_data(
                Reference(stats, min_col=column, min_row=4, max_row=last_row),
                titles_from_data=True,
            )
            series = chart.series[-1]
            series.tx.strRef.strCache = StrData(
                ptCount=1, pt=[StrVal(idx=0, v=stats.cell(4, column).value)]
            )
            series.cat = AxDataSource(strRef=StrRef(f=str(labels), strCache=categories))
            series.val.numRef.numCache = NumData(
                formatCode=number_format,
                ptCount=len(report.dimensions),
                pt=[
                    NumVal(idx=i, v=value)
                    for i in range(len(report.dimensions))
                    if (value := stats.cell(i + 5, column).value) is not None
                ],
            )
            series.graphicalProperties.solidFill = color
            series.graphicalProperties.line.noFill = True
        # Anchor the charts to the grid so they stay clear of the overview text when printed.
        top = 3 + index * (height_rows + 1)
        chart.anchor = TwoCellAnchor(
            _from=AnchorMarker(col=3, row=top),
            to=AnchorMarker(col=10, row=top + height_rows),
        )
        overview.add_chart(chart)

    overview.column_dimensions["C"].width = 4
    for index in range(4, 11):
        overview.column_dimensions[get_column_letter(index)].width = 11
    overview.print_area = f"A1:J{max(overview.max_row, len(charts) * (height_rows + 1) + 3)}"
    overview.page_setup.fitToHeight = 1 if len(names) <= 7 else 0


def _outline_sections(ws, sections: list[int]) -> None:
    ws.sheet_properties.outlinePr.summaryBelow = False
    ws.sheet_view.showOutlineSymbols = True
    for row, next_row in zip(sections, [*sections[1:], ws.max_row + 1], strict=True):
        if row + 1 < next_row:
            ws.row_dimensions.group(row + 1, next_row - 1, outline_level=1, hidden=True)
            ws.row_dimensions[row].collapsed = True


def _write_xlsx(workbook, report: ReportInput, sheets, checkpoint=None) -> bytes:
    workbook.remove(workbook.active)
    workbook.properties.creator = "未然 EDU"
    workbook.properties.title = "教师内部学情分析报告"
    workbook.properties.created = report.generated_at.astimezone(UTC).replace(tzinfo=None)
    for sheet in sheets:
        ws = workbook.create_sheet(sheet.name)
        ws.sheet_view.showGridLines = False
        overview = sheet.name == "测评概况"
        facts = sheet.name == "教师确认发现"
        statistics = sheet.name == "维度统计"
        audit = sheet.name == "技术附录"
        student_details = sheet.name == "授权学生明细"
        ws.freeze_panes = FREEZE_PANES.get(sheet.name)
        ws.sheet_properties.tabColor = TAB_COLORS[sheet.name]
        ws.sheet_view.zoomScale = 90 if overview else 100
        text_cell(ws["A2"], "未然 EDU 学情分析报告" if sheet.name == "测评概况" else sheet.name)
        ws["A2"].font = Font(name=FONT, size=16, bold=True, color=INK)
        ws.row_dimensions[2].height = 29
        ws.row_dimensions[3].height = 8
        widths = (25, 54) if overview else sheet.widths
        for index, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(index)].width = width
            ws.cell(3, index).border = Border(bottom=Side(style="thin", color=RULE))
        sections = []
        bar_rows = []
        previous_label = None
        for number, row in enumerate(sheet.rows, 4):
            if checkpoint is not None:
                checkpoint()
            label = row.cells[0].value
            key_count = overview and label in (
                "测评记录数",
                "统计项目数",
                "教师已确认发现数",
            )
            if row.section and (facts or audit):
                sections.append(number)
            if row.percentage_bar:
                bar_rows.append(number)
            student_row = student_details and type(label) is int
            new_group = (row.percentage_bar or student_row) and label != previous_label
            previous_label = label
            striped = (label if student_row else number) % 2 == 0
            occupied = len(row.cells)
            full_width = row.span or row.section and occupied == 1
            if full_width and occupied < len(widths):
                ws.merge_cells(
                    start_row=number, end_row=number, start_column=occupied, end_column=len(widths)
                )
            row_widths = (
                (*widths[: occupied - 1], sum(widths[occupied - 1 :])) if full_width else widths
            )
            for index, item in enumerate(row.cells, 1):
                cell = ws.cell(number, index)
                value = item.value
                if isinstance(value, str):
                    text_cell(cell, value)
                else:
                    cell.value = float(value) if isinstance(value, Fraction) else value
                cell.number_format = item.format
                warning = (
                    facts
                    and row.section
                    and index == 3
                    and value in EVIDENCE_WARNINGS
                    or statistics
                    and index in (7, 8)
                    and type(value) is int
                    and value > 0
                )
                emphasize = (
                    key_count
                    or statistics
                    and not row.percentage_bar
                    and index == 3
                    or facts
                    and row.section
                    and index == 2
                    or sheet.name == "选定群体"
                    and index == 2
                    and label == "符合条件人数"
                )
                cell.font = Font(
                    name=FONT,
                    size=15 if key_count and index == 2 else 11,
                    color="FFFFFF"
                    if row.header
                    else WARNING_INK
                    if warning
                    else BLUE
                    if emphasize and (not key_count or index == 2)
                    else INK,
                    bold=row.header
                    or row.section
                    or emphasize
                    or warning
                    or new_group
                    and index == 1,
                )
                numeric = not row.span and isinstance(value, (int, float, Fraction, datetime))
                centered = (row.header and not row.span) or (facts and row.section and index == 3)
                cell.alignment = Alignment(
                    vertical="center",
                    wrap_text=True,
                    horizontal="center" if centered else "right" if numeric else "left",
                    indent=0 if centered else 1,
                )
                if row.header:
                    cell.fill = PatternFill("solid", fgColor=INK)
                elif warning:
                    cell.fill = PatternFill("solid", fgColor=WARNING_FILL)
                elif row.section or key_count:
                    cell.fill = PatternFill("solid", fgColor=SECTION)
                    cell.border = Border(bottom=Side(style="thin", color=RULE))
                elif striped:
                    cell.fill = PatternFill("solid", fgColor=STRIPE)
                if overview and index == 1 and not row.header and not key_count:
                    cell.font = Font(name=FONT, size=11, color=MUTED)
                if new_group:
                    cell.border = Border(top=Side(style="thin", color=RULE))
            if full_width or row.section:
                for index in range(occupied + 1, len(widths) + 1):
                    cell = ws.cell(number, index)
                    if row.header:
                        cell.fill = PatternFill("solid", fgColor=INK)
                    elif row.section:
                        cell.fill = PatternFill("solid", fgColor=SECTION)
                        cell.border = Border(bottom=Side(style="thin", color=RULE))
                    else:
                        cell.fill = PatternFill("solid", fgColor=STRIPE if striped else "FFFFFF")
            height = _height(tuple(c.value for c in row.cells), row_widths)
            ws.row_dimensions[number].height = max(height, 28 if key_count or row.section else 23)
        if sections:
            _outline_sections(ws, sections)
        if bar_rows:
            ws.conditional_formatting.add(
                f"D{bar_rows[0]}:D{bar_rows[-1]}",
                DataBarRule(
                    start_type="num",
                    start_value=0,
                    end_type="num",
                    end_value=1,
                    color="BBCFFB",
                    showValue=True,
                ),
            )
        if not overview:
            ws.print_title_rows = "1:5" if facts else "1:3"
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.print_area = f"A1:{get_column_letter(len(sheet.widths))}{ws.max_row}"
    _summary_charts(workbook, report)
    stream = BytesIO()
    if checkpoint is not None:
        checkpoint()
    workbook.save(stream)
    return _canonical_zip(stream.getvalue(), report.generated_at, checkpoint)


def md_text(value: object) -> str:
    text = html.escape(str(value), quote=True).replace("\r", "\\r").replace("\n", "\\n")
    for symbol in ("\\", "|", "*", "_", "[", "]", "`"):
        text = text.replace(symbol, "\\" + symbol)
    return text


def _md_value(cell: Cell) -> str:
    value = cell.value
    if value is None:
        return "—"
    if isinstance(value, Fraction):
        return display(value, percent="%" in cell.format)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _markdown(report: ReportInput, sheets, checkpoint=None) -> str:
    """Use the same source metrics and format semantics as the downloadable workbook."""
    lines = [
        "# 未然 EDU 学情分析报告" + ("（合成演示）" if report.synthetic else ""),
        "",
        "**教师内部使用，非公开。**",
        "",
    ]
    for sheet in sheets:
        technical = sheet.name == "技术附录"
        if technical:
            lines += ["## 技术附录", "", "<details>", "<summary>展开溯源与精确值核查</summary>", ""]
        else:
            lines += [f"## {sheet.name}", ""]
        block = []

        def flush(block=block, lines=lines):
            if not block:
                return
            width = max(len(row.cells) for row in block)
            first = block[0]
            if first.header:
                header = [md_text(_md_value(c)) for c in first.cells]
                lines.append("| " + " | ".join(header + [""] * (width - len(header))) + " |")
                lines.append("| " + " | ".join(["---"] * width) + " |")
                for row in block[1:]:
                    values = [md_text(_md_value(c)) for c in row.cells]
                    lines.append("| " + " | ".join(values + [""] * (width - len(values))) + " |")
            else:
                for row in block:
                    values = [md_text(_md_value(c)) for c in row.cells]
                    lines.append("：".join(v for v in values if v))
                    lines.append("")
            lines.append("")
            block.clear()

        for row in sheet.rows:
            if checkpoint is not None:
                checkpoint()
            if row.section:
                flush()
                lines += ["### " + md_text(row.cells[0].value), ""]
            else:
                if row.header:
                    flush()
                block.append(row)
        flush()
        if technical:
            lines += ["</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


def generate_report(
    report: ReportInput,
    *,
    include_details: bool = False,
    authorization: DetailAuthorization | None = None,
    limits: ExportLimits | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> ReportFiles:
    """Atomic in-memory pair. Never authenticates, writes paths, or returns partial output."""
    limits = ExportLimits() if limits is None else limits
    validate(report, limits, include_details, authorization)
    if checkpoint is not None:
        checkpoint()
    sheets = plan(report, include_details, limits, checkpoint)
    markdown = _markdown(report, sheets, checkpoint)
    require(len(markdown.encode("utf-8")) <= limits.max_file_bytes, "report_file_size_limit")
    xlsx = _xlsx(
        report, plan(report, include_details, limits, checkpoint, spreadsheet=True), checkpoint
    )
    require(len(xlsx) <= limits.max_file_bytes, "report_file_size_limit")
    if checkpoint is not None:
        checkpoint()
    return ReportFiles(xlsx, markdown)
