from dataclasses import replace
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.importing.edu import ImportFileError, ParseLimits, parse_excel
from app.importing.edu.preview import build_preview

from .conftest import rewrite


def codes(result):
    return {i.code for i in result.issues}


def test_complete_template_preserves_precision_and_provenance(workbook, pack):
    result = parse_excel(pack(workbook))
    assert result.can_commit
    assert len(result.students) == 2
    assert len(result.rubrics) == 5
    assert result.students[0].student_number == "000000000001"
    assert result.students[1].student_number == "000000000002"
    assert result.students[0].raw_identity == "000000000001 合成同学0"
    assert result.students[0].name == "合成同学0"
    evaluations = result.students[0].evaluations
    assert [e.score_units for e in evaluations] == [100, 275, 350, 450, 500]
    assert evaluations[1].score_raw == "2.75"
    assert evaluations[1].comment_raw == "合成观察 0:2"
    assert evaluations[1].source.score_cell == "C4"
    assert evaluations[1].source.comment_cell == "D4"
    assert evaluations[1].source.file_sha256 == result.file_sha256
    assert result.rubrics[0].text_cells[0][0] == "L3"
    assert result.content_sha256 and len(result.content_sha256) == 64


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "SCORE_REQUIRED"),
        (True, "SCORE_NOT_NUMERIC"),
        ("3", "SCORE_NOT_NUMERIC"),
        ("隐藏字符串", "SCORE_NOT_NUMERIC"),
        (0, "SCORE_OUT_OF_RANGE"),
        (5.01, "SCORE_OUT_OF_RANGE"),
        (3.141, "SCORE_PRECISION"),
    ],
)
def test_invalid_score_blocks_whole_import(workbook, pack, value, expected):
    workbook["Sheet2"]["C3"] = value
    result = parse_excel(pack(workbook))
    assert not result.can_commit
    assert result.content_sha256 is None
    assert expected in codes(result)
    preview = build_preview(result)
    assert preview.status == "invalid"
    assert preview.summary.complete_student_count == 1
    assert preview.rows[0].score_raw is None
    assert preview.rows[0].score_state == ("missing" if value is None else "invalid")
    assert expected in preview.rows[0].issue_codes


@pytest.mark.parametrize(
    "raw",
    [
        b"2.750000000000000000000000000001",
        b"2.7500000000000001",
        b"2.751",
    ],
)
def test_exact_xml_lexeme_detects_precision_before_float_rounding(workbook, pack, raw):
    data = rewrite(
        pack(workbook),
        "xl/worksheets/sheet2.xml",
        lambda x: x.replace(b"<v>2.75</v>", b"<v>" + raw + b"</v>", 1),
    )
    result = parse_excel(data)
    assert "SCORE_PRECISION" in codes(result)
    assert result.students[0].evaluations[1].score_raw == raw.decode()


@pytest.mark.parametrize(
    "coordinate,value,expected",
    [
        ("A2", "错误身份", "STUDENT_IDENTITY_INVALID"),
        ("A2", None, "STUDENT_IDENTITY_INVALID"),
        ("B2", "错误表头", "BLOCK_HEADER_INVALID"),
        ("B3", "未知维度", "DIMENSION_UNKNOWN"),
        ("B3", None, "DIMENSION_UNKNOWN"),
        ("B3", "代码实现能力", "DIMENSION_DUPLICATE"),
        ("D3", 42, "COMMENT_TYPE_INVALID"),
        ("E3", "多余内容", "UNEXPECTED_DATA_COLUMN"),
    ],
)
def test_quality_errors_are_located_without_echo(workbook, pack, coordinate, value, expected):
    workbook["Sheet2"][coordinate] = value
    result = parse_excel(pack(workbook))
    assert not result.can_commit
    assert expected in codes(result)
    assert build_preview(result).summary.error_count > 0


def test_missing_dimension_and_duplicate_student(workbook, pack):
    workbook["Sheet2"].delete_rows(7)
    result = parse_excel(pack(workbook))
    assert "DIMENSION_MISSING" in codes(result)
    assert not result.can_commit

    # Find the second header after deletion; student identity is keyed by number, not name.
    workbook["Sheet2"]["A7"] = "000000000001-另一合成姓名"
    result = parse_excel(pack(workbook))
    assert "STUDENT_DUPLICATE" in codes(result)
    assert not any(s.complete for s in result.students)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_comments_warn_and_preserve_original(workbook, pack, value):
    workbook["Sheet2"]["D3"] = value
    result = parse_excel(pack(workbook))
    assert result.can_commit
    assert "COMMENT_EMPTY" in codes(result)
    assert build_preview(result).rows[0].comment_status == "empty"
    # Excel does not distinguish saved empty text from an absent value.
    assert result.students[0].evaluations[0].comment_raw == (value if value else None)


def test_repeated_comments_warn_without_dropping(workbook, pack):
    workbook["Sheet2"]["D4"] = workbook["Sheet2"]["D3"].value
    result = parse_excel(pack(workbook))
    assert result.can_commit
    assert sum(i.code == "COMMENT_REPEATED" for i in result.issues) == 2
    assert len(result.students[0].evaluations) == 5


@pytest.mark.parametrize("mutation", ["title", "missing", "merge"])
def test_rubric_mapping_is_required(workbook, pack, mutation):
    if mutation == "title":
        workbook["Sheet1"]["B2"] = "不匹配"
    elif mutation == "missing":
        workbook["Sheet1"]["L3"] = None
    else:
        workbook["Sheet1"].unmerge_cells("L3:P6")
    result = parse_excel(pack(workbook))
    assert not result.can_commit
    assert codes(result) & {"RUBRIC_REQUIRED", "RUBRIC_MAPPING_INVALID"}


def test_reordered_dimension_rows_and_student_blocks_are_supported(workbook, pack):
    before = parse_excel(pack(workbook))
    sheet = workbook["Sheet2"]
    rows = [[sheet.cell(r, c).value for c in range(1, 5)] for r in range(2, 14)]
    rows = rows[6:] + rows[:6]
    rows[1:6] = reversed(rows[1:6])
    for r, values in enumerate(rows, 2):
        for c, value in enumerate(values, 1):
            sheet.cell(r, c).value = value
    after = parse_excel(pack(workbook))
    assert after.can_commit
    assert before.content_sha256 == after.content_sha256
    assert before.file_sha256 != after.file_sha256


def test_empty_template_and_orphan_rows_fail(workbook, pack):
    sheet = workbook["Sheet2"]
    sheet.delete_rows(2, 100)
    result = parse_excel(pack(workbook))
    assert "NO_STUDENTS" in codes(result)
    sheet["B3"] = "算法与数据结构"
    result = parse_excel(pack(workbook))
    assert "ORPHAN_ROW" in codes(result)


def test_first_row_cannot_silently_hide_a_score(workbook, pack):
    workbook["Sheet2"]["C1"] = 3
    result = parse_excel(pack(workbook))
    assert not result.can_commit
    assert "TITLE_ROW_INVALID" in codes(result)


def test_sheet_names_and_hidden_sheets_are_rejected(workbook, pack):
    workbook["Sheet2"].title = "其他"
    with pytest.raises(ImportFileError, match="UNSUPPORTED_SHEETS"):
        parse_excel(pack(workbook))
    workbook["其他"].title = "Sheet2"
    workbook["Sheet2"].sheet_state = "hidden"
    with pytest.raises(ImportFileError, match="UNSUPPORTED_SHEETS"):
        parse_excel(pack(workbook))


@pytest.mark.parametrize("sheet,coordinate", [("Sheet2", "C3"), ("Sheet1", "L3")])
def test_formulas_are_never_evaluated_even_if_cached(workbook, pack, sheet, coordinate):
    workbook[sheet][coordinate] = "=1+2"
    with pytest.raises(ImportFileError, match="FORMULA_NOT_SUPPORTED"):
        parse_excel(pack(workbook))


def test_external_relationship_is_rejected(workbook, pack):
    workbook["Sheet2"]["D3"].hyperlink = "https://example.invalid/private-value"
    with pytest.raises(ImportFileError, match="EXTERNAL_LINK_NOT_SUPPORTED"):
        parse_excel(pack(workbook))


@pytest.mark.parametrize(
    "limit,value,code",
    [
        ("compressed_bytes", 10, "FILE_SIZE_LIMIT"),
        ("expanded_bytes", 10, "EXPANDED_SIZE_LIMIT"),
        ("zip_entries", 1, "ZIP_ENTRY_LIMIT"),
        ("rows_per_sheet", 5, "WORKSHEET_SIZE_LIMIT"),
        ("columns_per_sheet", 4, "WORKSHEET_SIZE_LIMIT"),
        ("cells_per_sheet", 5, "WORKSHEET_SIZE_LIMIT"),
        ("xml_elements_per_part", 5, "XML_ELEMENT_LIMIT"),
        ("seconds", -1, "PARSE_TIME_LIMIT"),
    ],
)
def test_resource_limits(workbook, pack, limit, value, code):
    with pytest.raises(ImportFileError, match=code):
        parse_excel(pack(workbook), limits=replace(ParseLimits(), **{limit: value}))


def test_declared_dimension_is_untrusted(workbook, pack):
    data = rewrite(
        pack(workbook),
        "xl/worksheets/sheet2.xml",
        lambda x: x.replace(b'ref="A1:D13"', b'ref="A1:XFD1048576"', 1),
    )
    assert parse_excel(data).can_commit


def test_xml_entities_and_duplicate_zip_members_fail_safely(workbook, pack):
    data = rewrite(
        pack(workbook),
        "xl/workbook.xml",
        lambda x: b'<!DOCTYPE workbook [<!ENTITY secret "private-value">]>' + x,
    )
    with pytest.raises(ImportFileError, match="INVALID_WORKBOOK"):
        parse_excel(data)
    original = pack(workbook)
    target = BytesIO(original)
    with ZipFile(target, "a", ZIP_DEFLATED) as archive:
        with pytest.warns(UserWarning):
            archive.writestr("xl/workbook.xml", b"duplicate")
    with pytest.raises(ImportFileError, match="ZIP_ENTRY_LIMIT"):
        parse_excel(target.getvalue())


@pytest.mark.parametrize("data", [b"", b"not a zip private-value", b"PK\x03\x04"])
def test_bad_files_have_safe_errors(data):
    with pytest.raises(ImportFileError) as error:
        parse_excel(data)
    assert "private-value" not in str(error.value)


@pytest.mark.parametrize("mutation", ["mismatched_row", "duplicate_cell", "row_order"])
def test_ambiguous_source_coordinates_fail(workbook, pack, mutation):
    def transform(xml):
        if mutation == "mismatched_row":
            return xml.replace(b'<c r="C3"', b'<c r="C4"', 1)
        if mutation == "duplicate_cell":
            return xml.replace(b'<c r="D3"', b'<c r="C3"', 1)
        return xml.replace(b'<row r="3"', b'<row r="1"', 1)

    data = rewrite(pack(workbook), "xl/worksheets/sheet2.xml", transform)
    with pytest.raises(ImportFileError, match="INVALID_CELL_COORDINATE"):
        parse_excel(data)


@pytest.mark.parametrize("identity", ["000000000001 - 合成姓名", "000000000001\t合成姓名"])
def test_supported_identity_delimiters(workbook, pack, identity):
    workbook["Sheet2"]["A2"] = identity
    result = parse_excel(pack(workbook))
    assert result.can_commit
    assert result.students[0].name == "合成姓名"
    assert result.students[0].student_number == "000000000001"


def test_scientific_notation_is_exact_and_changes_no_score(workbook, pack):
    before = parse_excel(pack(workbook))
    data = rewrite(
        pack(workbook),
        "xl/worksheets/sheet2.xml",
        lambda x: x.replace(b"<v>2.75</v>", b"<v>275e-2</v>", 1),
    )
    after = parse_excel(data)
    assert after.can_commit
    assert after.students[0].evaluations[1].score_raw == "275e-2"
    assert after.content_sha256 == before.content_sha256
