"""通用抽取层：把各种格式抽成"表格 + 文本行"的统一中间层。

这一层只回答"文件里有什么结构化内容"，**不判断业务含义**：
谁是学生、哪列是分数、哪个是测试点，属于 FR-02 的字段猜测与教师确认。
新增格式时在这里加一个分支即可，不碰双 Sheet 解析器和数据库。
"""

import csv
import io
from dataclasses import dataclass
from html.parser import HTMLParser
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as DefusedET
from openpyxl import load_workbook
from pypdf import PdfReader

from app.importing.edu import detect
from app.importing.edu.types import ImportFileError

WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

MAX_TABLES = 50
MAX_ROWS_PER_TABLE = 20000
MAX_CELLS_PER_ROW = 64
MAX_CELL_CHARS = 2000
MAX_TEXT_LINES = 20000
MAX_HTML_ENTRIES = 200
MAX_PDF_PAGES = 100
# 只在前几行内识别“说明行窄于主体”的结构异常，不做全表智能分析。
MAX_PREAMBLE_SCAN = 8


@dataclass(frozen=True)
class ExtractedTable:
    name: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ExtractedDocument:
    kind: str
    tables: tuple[ExtractedTable, ...]
    text_lines: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def extract_document(kind: str, data: bytes) -> ExtractedDocument:
    """按类型抽取；不认识的类型明确报错，不返回空结果冒充成功。"""
    if kind == detect.XLSX:
        tables, lines, warnings = _tables_from_xlsx(data)
        return _document(kind, tables, lines, warnings)
    if kind == detect.CSV:
        tables, lines, warnings = _tables_from_csv(data)
        return _document(kind, tables, lines, warnings)
    if kind == detect.TXT:
        return _document(kind, (), _lines_from_text(data))
    if kind == detect.HTML:
        tables, lines, warnings = _tables_from_html(data, "html")
        return _document(kind, tables, lines, warnings)
    if kind == detect.ZIP_HTML:
        tables, lines, warnings = _tables_from_zip_html(data)
        return _document(kind, tables, lines, warnings)
    if kind == detect.DOCX:
        tables, lines, warnings = _from_docx(data)
        return _document(kind, tables, lines, warnings)
    if kind == detect.PDF:
        return _document(kind, (), _lines_from_pdf(data))
    raise ImportFileError("EXTRACTION_UNSUPPORTED_KIND")


def _build_tables(
    raw: list[tuple[str, list[tuple[str, ...]]]],
) -> tuple[list[ExtractedTable], list[str], list[str]]:
    """统一走结构感知表头定位，汇集说明行与诊断。"""
    tables: list[ExtractedTable] = []
    lines: list[str] = []
    warnings: list[str] = []
    for name, rows in raw:
        table, preamble, table_warnings = _table(name, rows)
        tables.append(table)
        lines.extend(preamble)
        warnings.extend(f"{name}: {code}" for code in table_warnings)
    return tables, lines, warnings


def _document(
    kind: str,
    tables: tuple[ExtractedTable, ...],
    lines: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> ExtractedDocument:
    return ExtractedDocument(kind=kind, tables=tables, text_lines=lines, warnings=warnings)


def _numeric_cell(value: str) -> bool:
    try:
        float(value.strip())
    except ValueError:
        return False
    return True


def _table(name: str, rows: list[tuple[str, ...]]) -> tuple[ExtractedTable, list[str], list[str]]:
    """按行结构定位表头，不再默认“第一行就是表头”。

    - 宽度取全表最大行宽，所有行补齐；**绝不按表头宽度截断数据列**。
    - 前导行若明显窄于主体行（标题/说明行），移入文本行保留并给出诊断，
      而不是让它们占据表头位、吞掉后续字段。
    - 结构无法可靠判断时保留首行但显式标记 HEADER_UNCERTAIN，
      由下游按“待确认”处理，不冒充可信字段。
    """
    width = max(len(row) for row in rows)
    padded = [tuple(row) + ("",) * (width - len(row)) for row in rows]
    nonempty = [sum(bool(cell.strip()) for cell in row) for row in padded]
    dominant = max(nonempty)
    warnings: list[str] = []
    preamble: list[str] = []
    header_index = 0
    if dominant > 1 and nonempty[0] < dominant:
        first_full = next((index for index, count in enumerate(nonempty) if count == dominant), 0)
        if 0 < first_full < MAX_PREAMBLE_SCAN:
            # 说明行特征：显著窄于主体（不超过一半），且候选表头全是文本字段名。
            # 只是尾部略窄（如多一个逗号）不算说明行，避免误伤真实表头。
            narrow = all(nonempty[i] * 2 <= dominant for i in range(first_full))
            candidate_numeric = any(
                _numeric_cell(cell) for cell in padded[first_full] if cell.strip()
            )
            if narrow and not candidate_numeric:
                header_index = first_full
                warnings.append(f"HEADER_PREAMBLE_SKIPPED:{header_index}")
            elif narrow:
                # 显著窄行但候选表头含数值：证据不足，保留首行与全部数据并显式存疑。
                warnings.append("HEADER_UNCERTAIN")
            # 只是略窄（如尾部多一个逗号）：视为正常表头，不改认、不告警。
    header = padded[header_index]
    if dominant > 1 and any(_numeric_cell(cell) for cell in header if cell.strip()):
        # 表头位出现纯数值单元：可能是无表头数据，明确存疑而不是自信标注。
        warnings.append("HEADER_UNCERTAIN")
    for index in range(header_index):
        line = ", ".join(cell for cell in padded[index] if cell.strip())
        if line:
            preamble.append(f"{name}: {line}")
    body = tuple(padded[header_index + 1 :])
    warnings = list(dict.fromkeys(warnings))
    return ExtractedTable(name=name, header=header, rows=body), preamble, warnings


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)[:MAX_CELL_CHARS]


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFileError("EXTRACTION_ENCODING_UNKNOWN")


def _lines_from_text(data: bytes) -> tuple[str, ...]:
    text = _decode_text(data)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return tuple(lines[:MAX_TEXT_LINES])


def _tables_from_csv(data: bytes) -> tuple[list[ExtractedTable], list[str], list[str]]:
    text = _decode_text(data)
    rows = [
        tuple(cell[:MAX_CELL_CHARS] for cell in row[:MAX_CELLS_PER_ROW])
        for row in csv.reader(io.StringIO(text))
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        return [], [], []
    return _build_tables([("csv", rows[:MAX_ROWS_PER_TABLE])])


def _tables_from_xlsx(data: bytes) -> tuple[list[ExtractedTable], list[str], list[str]]:
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportFileError("EXTRACTION_WORKBOOK_UNREADABLE") from None
    raw: list[tuple[str, list[tuple[str, ...]]]] = []
    try:
        for sheet in book.worksheets:
            rows: list[tuple[str, ...]] = []
            for raw_row in sheet.iter_rows(values_only=True):
                cells = tuple(_cell(value) for value in (raw_row or ())[:MAX_CELLS_PER_ROW])
                if any(cells):
                    rows.append(cells)
                if len(rows) > MAX_ROWS_PER_TABLE:
                    raise ImportFileError("EXTRACTION_TOO_MANY_ROWS")
            if rows:
                raw.append((sheet.title, rows))
            if len(raw) >= MAX_TABLES:
                break
    finally:
        book.close()
    return _build_tables(raw)


class _HtmlTableParser(HTMLParser):
    """只收集 <table> 结构；不执行脚本、不解析样式。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[tuple[str, ...]]] = []
        self._rows: list[tuple[str, ...]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._rows = []
            self.tables.append(self._rows)
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip()[:MAX_CELL_CHARS])
            self._cell = None
        elif tag == "tr" and self._row is not None and self._rows is not None:
            if any(cell for cell in self._row):
                self._rows.append(tuple(self._row[:MAX_CELLS_PER_ROW]))
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _raw_html_rows(data: bytes, name: str) -> list[tuple[str, list[tuple[str, ...]]]]:
    parser = _HtmlTableParser()
    try:
        parser.feed(_decode_text(data))
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError("EXTRACTION_HTML_UNREADABLE") from None
    return [
        (f"{name}#{index}", rows[:MAX_ROWS_PER_TABLE])
        for index, rows in enumerate(parser.tables[:MAX_TABLES])
        if rows
    ]


def _tables_from_html(data: bytes, name: str) -> tuple[list[ExtractedTable], list[str], list[str]]:
    return _build_tables(_raw_html_rows(data, name))


def _tables_from_zip_html(data: bytes) -> tuple[list[ExtractedTable], list[str], list[str]]:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = [
                name
                for name in archive.namelist()
                if name.lower().endswith((".html", ".htm")) and not name.endswith("/")
            ][:MAX_HTML_ENTRIES]
            raw: list[tuple[str, list[tuple[str, ...]]]] = []
            for entry in entries:
                raw.extend(_raw_html_rows(archive.read(entry), entry))
                if len(raw) >= MAX_TABLES:
                    break
    except (BadZipFile, KeyError, OSError):
        raise ImportFileError("EXTRACTION_ZIP_UNREADABLE") from None
    return _build_tables(raw[:MAX_TABLES])


def _lines_from_pdf(data: bytes) -> tuple[str, ...]:
    """只抽文字层；扫描件（图片型 PDF）抽不到字，由调用方按空结果处理。"""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        raise ImportFileError("EXTRACTION_PDF_UNREADABLE") from None
    if reader.is_encrypted:
        raise ImportFileError("EXTRACTION_PDF_ENCRYPTED")
    lines: list[str] = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped[:MAX_CELL_CHARS])
            if len(lines) >= MAX_TEXT_LINES:
                return tuple(lines)
    return tuple(lines)


def _from_docx(
    data: bytes,
) -> tuple[tuple[ExtractedTable, ...], tuple[str, ...], list[str]]:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError, OSError):
        raise ImportFileError("EXTRACTION_DOCX_UNREADABLE") from None
    try:
        root = DefusedET.fromstring(xml)
    except Exception:
        raise ImportFileError("EXTRACTION_DOCX_UNREADABLE") from None

    raw: list[tuple[str, list[tuple[str, ...]]]] = []
    for index, block in enumerate(root.iter(WORD_NAMESPACE + "tbl")):
        rows: list[tuple[str, ...]] = []
        for row in block.iter(WORD_NAMESPACE + "tr"):
            cells = [
                "".join(node.text or "" for node in cell.iter(WORD_NAMESPACE + "t"))[
                    :MAX_CELL_CHARS
                ]
                for cell in row.iter(WORD_NAMESPACE + "tc")
            ]
            if any(cells):
                rows.append(tuple(cells[:MAX_CELLS_PER_ROW]))
        if rows:
            raw.append((f"table{index}", rows[:MAX_ROWS_PER_TABLE]))
        if len(raw) >= MAX_TABLES:
            break

    tables, preamble, warnings = _build_tables(raw)
    paragraphs = [
        "".join(node.text or "" for node in paragraph.iter(WORD_NAMESPACE + "t")).strip()
        for paragraph in root.iter(WORD_NAMESPACE + "p")
    ]
    lines = tuple(preamble + [text for text in paragraphs if text])[:MAX_TEXT_LINES]
    return tuple(tables), lines, warnings
