"""Bounded OOXML preflight before openpyxl; never extracts archive members."""

import posixpath
import re
from dataclasses import dataclass
from io import BytesIO
from time import monotonic
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as SafeET
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

from .types import ImportFileError, ParseLimits

CELL = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}\Z")


@dataclass(repr=False)
class ArchiveInfo:
    numeric: dict[str, dict[str, str]]
    bounds: dict[str, tuple[int, int]]
    merges: dict[str, set[str]]
    sheets: dict[str, str]


def check_time(deadline: float) -> None:
    if monotonic() > deadline:
        raise ImportFileError("PARSE_TIME_LIMIT")


def inspect_archive(data: bytes, limits: ParseLimits, deadline: float) -> ArchiveInfo:
    if not data or len(data) > limits.compressed_bytes:
        raise ImportFileError("FILE_SIZE_LIMIT")
    info = ArchiveInfo({}, {}, {}, {})
    relationships: dict[str, str] = {}
    sheet_ids: dict[str, str] = {}
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or len(names) > limits.zip_entries:
                raise ImportFileError("ZIP_ENTRY_LIMIT")
            if sum(entry.file_size for entry in entries) > limits.expanded_bytes:
                raise ImportFileError("EXPANDED_SIZE_LIMIT")
            for entry in entries:
                check_time(deadline)
                path = entry.filename
                if (
                    entry.flag_bits & 1
                    or "\\" in path
                    or path.startswith("/")
                    or ".." in path.split("/")
                ):
                    raise ImportFileError("UNSUPPORTED_ARCHIVE")
                if path.endswith("/"):
                    continue
                if not (path.endswith(".xml") or path.endswith(".rels")):
                    # The frozen template contains no macros, embedded objects or images.
                    raise ImportFileError("UNSUPPORTED_WORKBOOK_PART")
                worksheet = path.startswith("xl/worksheets/") and path.endswith(".xml")
                tokens: dict[str, str] = {}
                coordinates: set[str] = set()
                merges: set[str] = set()
                max_row = max_col = elements = 0
                current_row = last_row = last_col = 0
                # Reading is bounded by the already checked uncompressed sizes; CRC is checked too.
                with archive.open(entry) as stream:
                    for event, node in SafeET.iterparse(
                        stream, events=("start", "end"), forbid_dtd=True
                    ):
                        tag = node.tag.rsplit("}", 1)[-1]
                        if event == "start":
                            elements += 1
                            if elements > limits.xml_elements_per_part:
                                raise ImportFileError("XML_ELEMENT_LIMIT")
                            check_time(deadline)
                            if (
                                tag == "Relationship"
                                and node.get("TargetMode", "").lower() == "external"
                            ):
                                raise ImportFileError("EXTERNAL_LINK_NOT_SUPPORTED")
                            if tag == "Override" and "macroEnabled" in node.get("ContentType", ""):
                                raise ImportFileError("MACRO_NOT_SUPPORTED")
                            if worksheet and tag == "f":
                                raise ImportFileError("FORMULA_NOT_SUPPORTED")
                            if worksheet and tag == "row":
                                current_row = int(node.get("r", "0"))
                                if not 1 <= current_row <= limits.rows_per_sheet:
                                    raise ImportFileError("WORKSHEET_SIZE_LIMIT")
                                if current_row <= last_row:
                                    raise ImportFileError("INVALID_CELL_COORDINATE")
                                last_row, last_col = current_row, 0
                            continue
                        if node.text and len(node.text) > 32767:
                            raise ImportFileError("CELL_TEXT_LIMIT")
                        if path == "xl/_rels/workbook.xml.rels" and tag == "Relationship":
                            target = node.get("Target", "")
                            resolved = posixpath.normpath(
                                target.lstrip("/") if target.startswith("/") else "xl/" + target
                            )
                            if not resolved.startswith("xl/") or ":" in target:
                                raise ImportFileError("UNSUPPORTED_WORKBOOK_PART")
                            rid = node.get("Id", "")
                            if not rid or rid in relationships:
                                raise ImportFileError("INVALID_WORKBOOK")
                            relationships[rid] = resolved
                        if path == "xl/workbook.xml" and tag == "sheet":
                            name = node.get("name", "")
                            if name in sheet_ids or node.get("state", "visible") != "visible":
                                raise ImportFileError("UNSUPPORTED_SHEETS")
                            sheet_ids[name] = node.get(
                                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
                                "",
                            )
                        if worksheet and tag == "row":
                            row_number = int(node.get("r", "0"))
                            if not 1 <= row_number <= limits.rows_per_sheet:
                                raise ImportFileError("WORKSHEET_SIZE_LIMIT")
                            max_row = max(max_row, row_number)
                        if worksheet and tag == "c":
                            coordinate = node.get("r", "")
                            if not CELL.fullmatch(coordinate) or coordinate in coordinates:
                                raise ImportFileError("INVALID_CELL_COORDINATE")
                            col_letters, row_number = coordinate_from_string(coordinate)
                            col = column_index_from_string(col_letters)
                            if row_number != current_row or col <= last_col:
                                raise ImportFileError("INVALID_CELL_COORDINATE")
                            last_col = col
                            coordinates.add(coordinate)
                            max_row, max_col = max(max_row, row_number), max(max_col, col)
                            if (
                                max_row > limits.rows_per_sheet
                                or max_col > limits.columns_per_sheet
                                or len(coordinates) > limits.cells_per_sheet
                            ):
                                raise ImportFileError("WORKSHEET_SIZE_LIMIT")
                            value = next((c.text for c in node if c.tag.endswith("}v")), None)
                            if node.get("t", "n") == "n" and value is not None:
                                tokens[coordinate] = value
                            node.clear()
                        elif worksheet and tag == "mergeCell":
                            merges.add(node.get("ref", ""))
                            node.clear()
                        elif tag not in {"v", "t", "is", "r"}:
                            node.clear()
                if worksheet:
                    info.numeric[path], info.bounds[path], info.merges[path] = (
                        tokens,
                        (max_row, max_col),
                        merges,
                    )
            if set(sheet_ids) != {"Sheet1", "Sheet2"}:
                raise ImportFileError("UNSUPPORTED_SHEETS")
            info.sheets = {name: relationships[rid] for name, rid in sheet_ids.items()}
            if any(path not in info.bounds for path in info.sheets.values()):
                raise ImportFileError("INVALID_WORKBOOK")
            if len(set(info.sheets.values())) != 2:
                raise ImportFileError("INVALID_WORKBOOK")
    except ImportFileError:
        raise
    except (BadZipFile, KeyError, ValueError, OSError, RuntimeError):
        raise ImportFileError("INVALID_WORKBOOK") from None
    return info
