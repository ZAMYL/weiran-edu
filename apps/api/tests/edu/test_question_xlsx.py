"""Synthetic workbook coverage; no teacher source files."""

from fractions import Fraction
from io import BytesIO
from zipfile import ZipFile

import pytest
from openpyxl import Workbook

from app.importing.edu.questions import ITEM_IDS, PROTOCOL, has_question_marker, parse_questions
from app.importing.edu.types import ImportFileError


def synthetic(change=None):
    book = Workbook()
    definition = book.active
    definition.title = "Sheet1"
    definition.append([PROTOCOL, "题目名称", "最低分", "最高分", "步长", "单位"])
    for i, key in enumerate(ITEM_IDS, 1):
        definition.append([key, f"原始题目{i}", 0, i * 10, 0.01, "分"])
    scores = book.create_sheet("Sheet2")
    scores.append(["参评记录", *ITEM_IDS])
    for i in range(20):
        scores.append([f"S{i:04d}", *([2.75] * 13)])
    if change:
        change(book)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def test_original_questions_scales_scores_and_sources():
    assert has_question_marker(synthetic())
    parsed = parse_questions(synthetic())
    assert len(parsed.questions) == 13
    assert len(parsed.participants) == 20
    assert len(parsed.observations) == 260
    assert {i.kind for i in parsed.questions} == {"question"}
    assert [i.maximum for i in parsed.questions] == [str(i * 10) for i in range(1, 14)]
    assert parsed.observations[0].exact == Fraction(11, 4)
    assert parsed.observations[0].raw == "2.75"
    assert parsed.observations[0].source_cell == "B2"
    assert parsed.questions[0].source_cell == "A2:F2"
    assert len(parsed.file_sha256) == 64


def test_protocol_text_elsewhere_does_not_steal_legacy_dispatch():
    assert not has_question_marker(
        synthetic(lambda w: setattr(w["Sheet1"]["A1"], "value", "旧模板"))
    )
    assert has_question_marker(synthetic())


@pytest.mark.parametrize(
    "value,state",
    [
        (None, "missing"),
        (True, "invalid_numeric"),
        ("private text", "invalid_numeric"),
        (-1, "out_of_range"),
        (11, "out_of_range"),
        (1.001, "off_step"),
        (0, "valid"),
    ],
)
def test_distinct_quality_states_preserve_denominator(value, state):
    parsed = parse_questions(synthetic(lambda b: setattr(b["Sheet2"]["B2"], "value", value)))
    assert parsed.observations[0].state == state
    assert len(parsed.observations) == 260
    assert len(parsed.participants) == 20
    if state != "valid":
        assert parsed.observations[0].exact is None


@pytest.mark.parametrize(
    "sheet,cell,value,code",
    [
        ("Sheet1", "C2", None, "QUESTION_SCALE_REQUIRED"),
        ("Sheet1", "E2", 0, "QUESTION_SCALE_REQUIRED"),
        ("Sheet1", "A3", "Q01", "QUESTION_DEFINITION_REQUIRED"),
        ("Sheet2", "C1", "能力", "QUESTION_SCORE_COLUMNS_REQUIRED"),
        ("Sheet2", "A3", "S0000", "QUESTION_PARTICIPANT_DUPLICATE"),
        ("Sheet2", "B2", "=1+1", "FORMULA_NOT_SUPPORTED"),
    ],
)
def test_fail_closed_with_fixed_codes(sheet, cell, value, code):
    raw = synthetic(lambda b: setattr(b[sheet][cell], "value", value))
    with pytest.raises(ImportFileError) as error:
        parse_questions(raw)
    assert error.value.code == code
    assert "private" not in str(error.value)


def test_original_bytes_and_snapshot_binding():
    a = parse_questions(synthetic())
    b = parse_questions(synthetic(lambda w: setattr(w["Sheet2"]["B2"], "value", 3)))
    assert a.file_sha256 != b.file_sha256
    assert a.observations[0].exact != b.observations[0].exact


def test_untrusted_declared_dimensions_do_not_drive_iteration():
    original = synthetic()
    target = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(target, "w") as output:
        for entry in source.infolist():
            raw = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet2.xml":
                raw = raw.replace(b'<dimension ref="A1:N21"', b'<dimension ref="A1:XFD1048576"')
            output.writestr(entry, raw)
    parsed = parse_questions(target.getvalue())
    assert len(parsed.observations) == 260
