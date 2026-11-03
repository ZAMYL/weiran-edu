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
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .layout import Cell, display, plan
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


def _write_xlsx(workbook, report: ReportInput, sheets, checkpoint=None) -> bytes:
    workbook.remove(workbook.active)
    workbook.properties.creator = "未然 EDU"
    workbook.properties.title = "教师内部学情分析报告"
    workbook.properties.created = report.generated_at.astimezone(UTC).replace(tzinfo=None)
    for sheet in sheets:
        ws = workbook.create_sheet(sheet.name)
        ws.sheet_view.showGridLines = False
        ws.freeze_panes = "B5" if len(sheet.rows) > 18 else None
        ws.sheet_properties.tabColor = "8996A0" if sheet.name == "技术附录" else "244B73"
        text_cell(ws["A2"], "未然 EDU 学情分析报告" if sheet.name == "测评概况" else sheet.name)
        ws["A2"].font = Font(name="Microsoft YaHei", size=16, bold=True, color="243746")
        ws.row_dimensions[2].height = 28
        for index, width in enumerate(sheet.widths, 1):
            ws.column_dimensions[get_column_letter(index)].width = width
            ws.cell(3, index).border = Border(bottom=Side(style="thin", color="A9B5BE"))
        for number, row in enumerate(sheet.rows, 4):
            if checkpoint is not None:
                checkpoint()
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
                    bold=row.header or row.section,
                )
                cell.alignment = Alignment(
                    vertical="center", wrap_text=True, horizontal="center" if row.header else None
                )
                if row.header:
                    cell.fill = PatternFill("solid", fgColor="344653")
                elif row.section:
                    cell.fill = PatternFill("solid", fgColor="DCE7EF")
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
    """Use the same section plan and format semantics as the downloadable workbook."""
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
    xlsx = _xlsx(report, sheets, checkpoint)
    require(len(xlsx) <= limits.max_file_bytes, "report_file_size_limit")
    if checkpoint is not None:
        checkpoint()
    return ReportFiles(xlsx, markdown)
