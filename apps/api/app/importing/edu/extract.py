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
        return _document(kind, _tables_from_xlsx(data))
    if kind == detect.CSV:
        return _document(kind, _tables_from_csv(data))
    if kind == detect.TXT:
        return _document(kind, (), _lines_from_text(data))
    if kind == detect.HTML:
        return _document(kind, _tables_from_html(data, "html"))
    if kind == detect.ZIP_HTML:
        return _document(kind, _tables_from_zip_html(data))
    if kind == detect.DOCX:
        tables, lines = _from_docx(data)
        return _document(kind, tables, lines)
    if kind == detect.PDF:
        return _document(kind, (), _lines_from_pdf(data))
    raise ImportFileError("EXTRACTION_UNSUPPORTED_KIND")


def _document(
    kind: str,
    tables: tuple[ExtractedTable, ...],
    lines: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> ExtractedDocument:
    return ExtractedDocument(kind=kind, tables=tables, text_lines=lines, warnings=warnings)


def _table(name: str, rows: list[tuple[str, ...]]) -> ExtractedTable:
    """第一行当表头；行长度补齐，避免下游按列索引时越界。"""
    header = rows[0]
    width = len(header)
    body = tuple(tuple(row[:width]) + ("",) * (width - len(row)) for row in rows[1:])
    return ExtractedTable(name=name, header=header, rows=body)


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


def _tables_from_csv(data: bytes) -> tuple[ExtractedTable, ...]:
    text = _decode_text(data)
    rows = [
        tuple(cell[:MAX_CELL_CHARS] for cell in row[:MAX_CELLS_PER_ROW])
        for row in csv.reader(io.StringIO(text))
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        return ()
    return (_table("csv", rows[:MAX_ROWS_PER_TABLE]),)


def _tables_from_xlsx(data: bytes) -> tuple[ExtractedTable, ...]:
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportFileError("EXTRACTION_WORKBOOK_UNREADABLE") from None
    tables: list[ExtractedTable] = []
    try:
        for sheet in book.worksheets:
            rows: list[tuple[str, ...]] = []
            for raw in sheet.iter_rows(values_only=True):
                cells = tuple(_cell(value) for value in (raw or ())[:MAX_CELLS_PER_ROW])
                if any(cells):
                    rows.append(cells)
                if len(rows) > MAX_ROWS_PER_TABLE:
                    raise ImportFileError("EXTRACTION_TOO_MANY_ROWS")
            if rows:
                tables.append(_table(sheet.title, rows))
            if len(tables) >= MAX_TABLES:
                break
    finally:
        book.close()
    return tuple(tables)


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


def _tables_from_html(data: bytes, name: str) -> tuple[ExtractedTable, ...]:
    parser = _HtmlTableParser()
    try:
        parser.feed(_decode_text(data))
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError("EXTRACTION_HTML_UNREADABLE") from None
    tables: list[ExtractedTable] = []
    for index, rows in enumerate(parser.tables[:MAX_TABLES]):
        if rows:
            tables.append(_table(f"{name}#{index}", rows[:MAX_ROWS_PER_TABLE]))
    return tuple(tables)


def _tables_from_zip_html(data: bytes) -> tuple[ExtractedTable, ...]:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = [
                name
                for name in archive.namelist()
                if name.lower().endswith((".html", ".htm")) and not name.endswith("/")
            ][:MAX_HTML_ENTRIES]
            tables: list[ExtractedTable] = []
            for entry in entries:
                tables.extend(_tables_from_html(archive.read(entry), entry))
                if len(tables) >= MAX_TABLES:
                    break
    except (BadZipFile, KeyError, OSError):
        raise ImportFileError("EXTRACTION_ZIP_UNREADABLE") from None
    return tuple(tables[:MAX_TABLES])


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
) -> tuple[tuple[ExtractedTable, ...], tuple[str, ...]]:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (BadZipFile, KeyError, OSError):
        raise ImportFileError("EXTRACTION_DOCX_UNREADABLE") from None
    try:
        root = DefusedET.fromstring(xml)
    except Exception:
        raise ImportFileError("EXTRACTION_DOCX_UNREADABLE") from None

    tables: list[ExtractedTable] = []
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
            tables.append(_table(f"table{index}", rows[:MAX_ROWS_PER_TABLE]))
        if len(tables) >= MAX_TABLES:
            break

    paragraphs = [
        "".join(node.text or "" for node in paragraph.iter(WORD_NAMESPACE + "t")).strip()
        for paragraph in root.iter(WORD_NAMESPACE + "p")
    ]
    lines = tuple(text for text in paragraphs if text)[:MAX_TEXT_LINES]
    return tuple(tables), lines
