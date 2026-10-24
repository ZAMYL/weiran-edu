"""Parse the frozen two-sheet teacher template without persistence or network IO."""

import hashlib
import json
import re
import warnings
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from io import BytesIO
from time import monotonic
from uuid import uuid4

from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter, range_boundaries

from .archive import ArchiveInfo, check_time, inspect_archive
from .types import (
    DIMENSIONS,
    Evaluation,
    ImportFileError,
    Issue,
    ParsedWorkbook,
    ParseLimits,
    Rubric,
    Source,
    StudentBlock,
)

IDENTITY = re.compile(r"([0-9]{12})(?:[ \t]*-[ \t]*|[ \t]+)([^\s-](?:[^\r\n]*\S)?)\Z")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
LABELS = {spec.label: spec.code for spec in DIMENSIONS}


@dataclass(frozen=True, repr=False)
class InputCell:
    row: int
    coordinate: str
    value: object
    data_type: str


type Row = tuple[InputCell, ...]


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _issue(code: str, row: int, column: str = "B", *, warning: bool = False) -> Issue:
    return Issue(code, "warning" if warning else "error", "Sheet2", f"{column}{row}", row)


def _score(cell: InputCell, numeric: dict[str, str]) -> tuple[int | None, object, str | None]:
    value = cell.value
    if value is None:
        return None, None, "SCORE_REQUIRED"
    raw = numeric.get(cell.coordinate, value)
    if cell.data_type != "n" or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, raw, "SCORE_NOT_NUMERIC"
    if not isinstance(raw, str) or len(raw) > 80 or not NUMBER.fullmatch(raw):
        return None, raw, "SCORE_INVALID"
    try:
        score = Decimal(raw)
        if not score.is_finite() or not Decimal(1) <= score <= Decimal(5):
            return None, raw, "SCORE_OUT_OF_RANGE"
        # Exact tuple arithmetic: Decimal multiplication under the default context can round
        # very long OOXML numeric lexemes before the precision check.
        numerator, denominator = score.as_integer_ratio()
        units, remainder = divmod(numerator * 100, denominator)
        if remainder:
            return None, raw, "SCORE_PRECISION"
        return units, raw, None
    except (InvalidOperation, ValueError, OverflowError):
        return None, raw, "SCORE_INVALID"


def _rubrics(rows: dict[int, Row], archive: ArchiveInfo) -> tuple[list[Rubric], list[Issue]]:
    rubrics: list[Rubric] = []
    issues: list[Issue] = []
    merges = archive.merges[archive.sheets["Sheet1"]]
    for spec in DIMENSIONS:
        title_cell = next(c for c in rows[2] if getattr(c, "coordinate", "") == spec.title_cell)
        if _text(title_cell.value) != spec.rubric_title:
            issues.append(Issue("RUBRIC_MAPPING_INVALID", "error", "Sheet1", spec.title_cell, 2))
        left, top, right, bottom = range_boundaries(spec.rubric_range)
        cells = tuple(
            (c.coordinate, c.value)
            for row in range(top, bottom + 1)
            for c in rows.get(row, ())[left - 1 : right]
            if isinstance(c.value, str) and c.value.strip()
        )
        anchor = spec.rubric_range.split(":")[0]
        if spec.rubric_range not in merges or not any(coord == anchor for coord, _ in cells):
            issues.append(Issue("RUBRIC_REQUIRED", "error", "Sheet1", anchor, top))
        rubrics.append(
            Rubric(
                spec.code, str(title_cell.value or ""), spec.title_cell, spec.rubric_range, cells
            )
        )
    return rubrics, issues


def _block(
    header: Row,
    data: list[Row],
    index: int,
    file_hash: str,
    numeric: dict[str, str],
) -> tuple[StudentBlock, list[Issue]]:
    row = header[0].row
    raw_identity = header[0].value
    match = IDENTITY.fullmatch(raw_identity) if isinstance(raw_identity, str) else None
    issues: list[Issue] = []
    if match is None:
        issues.append(_issue("STUDENT_IDENTITY_INVALID", row, "A"))
    if tuple(_text(c.value) for c in header[1:4]) != ("维度", "评分（1~5）", "评语"):
        issues.append(_issue("BLOCK_HEADER_INVALID", row))
    evaluations: list[Evaluation] = []
    for cells in data:
        row_number = cells[1].row
        dimension = LABELS.get(_text(cells[1].value))
        if dimension is None:
            issues.append(_issue("DIMENSION_UNKNOWN", row_number))
        units, raw_score, error = _score(cells[2], numeric)
        if error:
            issues.append(_issue(error, row_number, "C"))
        comment = cells[3].value
        if comment is not None and not isinstance(comment, str):
            issues.append(_issue("COMMENT_TYPE_INVALID", row_number, "D"))
        if comment is None or isinstance(comment, str) and not comment.strip():
            issues.append(_issue("COMMENT_EMPTY", row_number, "D", warning=True))
        evaluations.append(
            Evaluation(
                dimension,
                units,
                raw_score,
                comment if isinstance(comment, str) else None,
                Source(
                    file_hash,
                    "Sheet2",
                    row_number,
                    f"B{row_number}",
                    f"C{row_number}",
                    f"D{row_number}",
                ),
            )
        )
    counts = Counter(e.dimension_code for e in evaluations if e.dimension_code)
    for spec in DIMENSIONS:
        if not counts[spec.code]:
            issues.append(_issue("DIMENSION_MISSING", row))
        elif counts[spec.code] > 1:
            issues.append(_issue("DIMENSION_DUPLICATE", row))
    return StudentBlock(
        str(uuid4()),
        f"学生 {index:03d}",
        raw_identity,
        match[1] if match else None,
        match[2] if match else None,
        row,
        tuple(evaluations),
        not any(issue.severity == "error" for issue in issues),
    ), issues


def _content_hash(students: list[StudentBlock], rubrics: list[Rubric]) -> str:
    # Private canonical representation; never write or log this JSON.
    dimension_order = {spec.code: index for index, spec in enumerate(DIMENSIONS)}
    content = {
        "version": "content.v1",
        "students": [
            [
                s.student_number,
                [
                    [e.dimension_code, e.score_units, e.comment_raw]
                    for e in sorted(s.evaluations, key=lambda e: dimension_order[e.dimension_code])
                ],
            ]
            for s in sorted(students, key=lambda s: s.student_number or "")
        ],
        "rubrics": [[r.dimension_code, r.title, [t for _, t in r.text_cells]] for r in rubrics],
    }
    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def parse_excel(data: bytes, *, limits: ParseLimits | None = None) -> ParsedWorkbook:
    """Private result. Use build_preview for serialization; errors never echo input.

    The caller must bound streaming uploads before accumulating ``data``. This function
    performs no network access and does not create files, batches or durable identities.
    """
    limits = limits or ParseLimits()
    deadline = monotonic() + limits.seconds
    try:
        return _parse(data, limits, deadline)
    except ImportFileError:
        raise
    except Exception:
        # Third-party reader exceptions may contain source strings or ZIP member names.
        raise ImportFileError("INVALID_WORKBOOK") from None


def _parse(data: bytes, limits: ParseLimits, deadline: float) -> ParsedWorkbook:
    archive = inspect_archive(data, limits, deadline)
    file_hash = hashlib.sha256(data).hexdigest()
    # openpyxl warnings may contain source strings. The service returns fixed issue codes.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        book = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
        try:
            all_rows: dict[str, dict[int, Row]] = {}
            for name in ("Sheet1", "Sheet2"):
                sheet = book[name]
                sheet.reset_dimensions()  # Do not trust the OOXML declared dimension.
                max_row, max_col = archive.bounds[archive.sheets[name]]
                all_rows[name] = {}
                for row_number, cells in enumerate(
                    sheet.iter_rows(
                        max_row=max(max_row, 22 if name == "Sheet1" else 2),
                        max_col=max(max_col, 16 if name == "Sheet1" else 4),
                    ),
                    1,
                ):
                    check_time(deadline)
                    all_rows[name][row_number] = tuple(
                        InputCell(
                            row_number,
                            f"{get_column_letter(col)}{row_number}",
                            c.value,
                            c.data_type,
                        )
                        for col, c in enumerate(cells, 1)
                    )
            rubrics, issues = _rubrics(all_rows["Sheet1"], archive)
            students: list[StudentBlock] = []
            header: Row | None = None
            block_rows: list[Row] = []
            numeric = archive.numeric[archive.sheets["Sheet2"]]

            def finish() -> None:
                if header is not None:
                    student, errors = _block(
                        header, block_rows, len(students) + 1, file_hash, numeric
                    )
                    students.append(student)
                    issues.extend(errors)

            for row_number, cells in all_rows["Sheet2"].items():
                if row_number == 1:
                    if any(c.value is not None for c in cells[1:]):
                        issues.append(_issue("TITLE_ROW_INVALID", 1))
                    continue  # Reserved title row in this fixed template.
                if any(c.value is not None for c in cells[4:]):
                    issues.append(_issue("UNEXPECTED_DATA_COLUMN", row_number, "E"))
                if not any(c.value is not None for c in cells[:4]):
                    continue
                if cells[0].value is not None or _text(cells[1].value) == "维度":
                    finish()
                    header, block_rows = cells, []
                elif header is None:
                    issues.append(_issue("ORPHAN_ROW", row_number))
                else:
                    block_rows.append(cells)
            finish()
            if not students:
                issues.append(_issue("NO_STUDENTS", 2, "A"))
            repeated_ids = Counter(s.student_number for s in students if s.student_number)
            students = [
                replace(s, complete=False) if repeated_ids[s.student_number] > 1 else s
                for s in students
            ]
            comments = Counter(
                e.comment_raw
                for s in students
                for e in s.evaluations
                if e.comment_raw and e.comment_raw.strip()
            )
            for s in students:
                if s.student_number and repeated_ids[s.student_number] > 1:
                    issues.append(_issue("STUDENT_DUPLICATE", s.header_row, "A"))
                for e in s.evaluations:
                    if comments[e.comment_raw] > 1:
                        issues.append(
                            _issue("COMMENT_REPEATED", e.source.row_number, "D", warning=True)
                        )
            can_commit = not any(i.severity == "error" for i in issues)
            check_time(deadline)
            return ParsedWorkbook(
                file_hash,
                _content_hash(students, rubrics) if can_commit else None,
                tuple(students),
                tuple(rubrics),
                tuple(issues),
            )
        finally:
            book.close()
