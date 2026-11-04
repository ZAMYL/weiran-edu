"""Private, explicit thirteen-question XLSX adapter; not a generic column detector.

Sheet1 defines Q01..Q13 and each original numeric scale. Sheet2 contains
anonymous participant keys and those score columns. No identity inference.
Reuses the V1 bounded OOXML safety preflight, including original numeric tokens.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
from time import monotonic
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring
from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter

from .archive import check_time, inspect_archive
from .types import ImportFileError, ParseLimits

PROTOCOL = "edu-question-xlsx.v1"
ITEM_IDS = tuple(f"Q{i:02d}" for i in range(1, 14))


def has_question_marker(data: bytes) -> bool:
    """Bounded dispatch hint only; never admission or a substitute for preflight."""
    if len(data) > ParseLimits().compressed_bytes:
        return False
    try:
        with ZipFile(BytesIO(data)) as archive:
            if len(archive.infolist()) > ParseLimits().zip_entries:
                return False
            with archive.open("xl/worksheets/sheet1.xml") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                return False
            root = fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
            cell = root.find(".//{*}c[@r='A1']")
            if cell is None:
                return False
            if cell.get("t") == "inlineStr":
                return "".join(n.text or "" for n in cell.findall(".//{*}t")) == PROTOCOL
            if cell.get("t") == "s":
                value = cell.find("{*}v")
                index = int(value.text) if value is not None else -1
                with archive.open("xl/sharedStrings.xml") as stream:
                    strings_raw = stream.read(65537)
                if len(strings_raw) <= 65536:
                    strings = fromstring(
                        strings_raw, forbid_dtd=True, forbid_entities=True, forbid_external=True
                    ).findall("{*}si")
                    if 0 <= index < len(strings):
                        return (
                            "".join(
                                n.text or "" for n in strings[index].iter() if n.tag.endswith("}t")
                            )
                            == PROTOCOL
                        )
    except (
        BadZipFile,
        KeyError,
        ValueError,
        OSError,
        RuntimeError,
        ParseError,
        DefusedXmlException,
    ):
        pass
    return False


def number(raw: str) -> Fraction:
    if len(raw) > 64 or not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", raw):
        raise ValueError("INVALID_DECIMAL")
    return Fraction(Decimal(raw))


@dataclass(frozen=True)
class Question:
    item_id: str
    original_name: str
    minimum: str
    maximum: str
    step: str
    unit: str
    source_cell: str
    kind: str = "question"


@dataclass(frozen=True, repr=False)
class QuestionScore:
    participant_id: str
    item_id: str
    raw: str | None
    state: str
    source_cell: str
    exact: Fraction | None = field(repr=False)


@dataclass(frozen=True, repr=False)
class QuestionWorkbook:
    file_sha256: str
    questions: tuple[Question, ...]
    participants: tuple[str, ...]
    observations: tuple[QuestionScore, ...]
    protocol: str = PROTOCOL


def parse_questions(data: bytes, limits: ParseLimits | None = None) -> QuestionWorkbook:
    limits = limits or ParseLimits()
    deadline = monotonic() + limits.seconds
    archive = inspect_archive(data, limits, deadline)
    if any(archive.merges.values()):
        raise ImportFileError("QUESTION_STRUCTURE_UNSUPPORTED")
    workbook = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
    try:
        definition, scores = workbook["Sheet1"], workbook["Sheet2"]
        definition.reset_dimensions()
        scores.reset_dimensions()
        definition_bounds = archive.bounds[archive.sheets["Sheet1"]]
        score_rows, score_columns = archive.bounds[archive.sheets["Sheet2"]]
        if definition_bounds != (14, 6) or score_columns != 14:
            raise ImportFileError("QUESTION_STRUCTURE_UNSUPPORTED")
        definitions = list(definition.iter_rows(max_row=14, max_col=6))
        if tuple(c.value for c in definitions[0]) != (
            PROTOCOL,
            "题目名称",
            "最低分",
            "最高分",
            "步长",
            "单位",
        ):
            raise ImportFileError("QUESTION_STRUCTURE_UNSUPPORTED")
        questions = []
        tokens = archive.numeric[archive.sheets["Sheet1"]]
        for index, row in enumerate(definitions[1:], 2):
            check_time(deadline)
            values = [c.value for c in row]
            if values[0] != ITEM_IDS[index - 2] or any(
                not isinstance(values[i], str) or not values[i].strip() or len(values[i]) > 100
                for i in (1, 5)
            ):
                raise ImportFileError("QUESTION_DEFINITION_REQUIRED")
            try:
                scale = tuple(tokens[f"{col}{index}"] for col in "CDE")
                minimum, maximum, step = map(number, scale)
                if minimum >= maximum or step <= 0:
                    raise ValueError("INVALID_SCALE")
            except (KeyError, ValueError):
                raise ImportFileError("QUESTION_SCALE_REQUIRED") from None
            questions.append(
                Question(values[0], values[1], *scale, values[5], f"A{index}:F{index}")
            )
        if tuple(c.value for c in next(scores.iter_rows(max_row=1, max_col=14))) != (
            "参评记录",
            *ITEM_IDS,
        ):
            raise ImportFileError("QUESTION_SCORE_COLUMNS_REQUIRED")
        tokens = archive.numeric[archive.sheets["Sheet2"]]
        participants: list[str] = []
        observations = []
        for row_number, row in enumerate(
            scores.iter_rows(min_row=2, max_row=score_rows, max_col=14), 2
        ):
            check_time(deadline)
            key = row[0].value
            if not isinstance(key, str) or not re.fullmatch(r"S[0-9]{4,8}", key):
                raise ImportFileError("QUESTION_PARTICIPANT_REQUIRED")
            if key in participants:
                raise ImportFileError("QUESTION_PARTICIPANT_DUPLICATE")
            participants.append(key)
            for column, (item, cell) in enumerate(zip(questions, row[1:], strict=True), 2):
                coordinate = f"{get_column_letter(column)}{row_number}"
                raw = None if cell.value is None else tokens.get(coordinate)
                value = None
                if cell.value is None:
                    state = "missing"
                elif cell.data_type != "n" or raw is None:
                    # Preserve invalid text privately, never expose it in a preview/error.
                    raw, state = str(cell.value), "invalid_numeric"
                else:
                    try:
                        value = number(raw)
                        if not number(item.minimum) <= value <= number(item.maximum):
                            state = "out_of_range"
                        elif ((value - number(item.minimum)) / number(item.step)).denominator != 1:
                            state = "off_step"
                        else:
                            state = "valid"
                    except ValueError:
                        state = "invalid_numeric"
                observations.append(
                    QuestionScore(
                        key,
                        item.item_id,
                        raw,
                        state,
                        coordinate,
                        value if state == "valid" else None,
                    )
                )
        if not participants:
            raise ImportFileError("QUESTION_PARTICIPANT_REQUIRED")
        return QuestionWorkbook(
            sha256(data).hexdigest(), tuple(questions), tuple(participants), tuple(observations)
        )
    finally:
        workbook.close()
