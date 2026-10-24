from app.importing.edu import parse_excel
from app.importing.edu.preview import build_preview


def test_preview_is_whitelisted_and_no_private_input_escapes(workbook, pack, caplog, capsys):
    sheet = workbook["Sheet2"]
    sheet["A2"] = "000000000001 私密合成姓名"
    sheet["D3"] = "私密评语，联系合成电话 19900000000"
    sheet["C3"] = 123456789012
    result = parse_excel(pack(workbook))
    public = build_preview(result).model_dump_json()
    for secret in ("000000000001", "私密合成姓名", "私密评语", "19900000000", "123456789012"):
        assert secret not in public
        assert secret not in repr(result)
        assert secret not in repr(result.students[0])
        assert secret not in repr(result.students[0].evaluations[0])
    assert build_preview(result).rows[0].comment is None
    assert build_preview(result).rows[0].comment_status == "withheld"
    assert not caplog.records
    assert capsys.readouterr().out == ""


def test_page_counts_and_transient_identifiers(workbook, pack):
    data = pack(workbook)
    result = parse_excel(data)
    first = build_preview(result, row_limit=3)
    second = build_preview(result, row_offset=3, row_limit=7)
    assert first.row_page.has_more
    assert not second.row_page.has_more
    assert first.summary.detected_student_count == 2
    assert first.summary.parsed_evaluation_count == 10
    assert len(first.rows) + len(second.rows) == 10
    assert first.rows[0].preview_student_id == second.rows[0].preview_student_id
    assert (
        first.rows[0].preview_student_id
        != build_preview(parse_excel(data)).rows[0].preview_student_id
    )
    assert first.rows[1].score == 2.75
    assert first.rows[1].score_raw == "2.75"


def test_student_pages_only_include_complete_students(workbook, pack):
    result = parse_excel(pack(workbook))
    first = build_preview(result, student_limit=1)
    second = build_preview(result, student_offset=1, student_limit=1)
    assert first.student_page.has_more and not second.student_page.has_more
    assert len(first.student_samples[0].scores) == 5
    assert first.student_samples[0].preview_student_id == first.rows[0].preview_student_id
    assert (
        first.student_samples[0].preview_student_id != second.student_samples[0].preview_student_id
    )
    workbook["Sheet2"]["C3"] = None
    invalid = build_preview(parse_excel(pack(workbook)))
    assert invalid.status == "invalid"
    assert invalid.summary.complete_student_count == len(invalid.student_samples) == 1
    assert invalid.student_samples[0].display_label == "学生 002"
    assert len(invalid.rows) == 10  # Invalid rows remain available for diagnosis.


def test_header_errors_are_visible_on_the_student_rows(workbook, pack):
    workbook["Sheet2"]["A8"] = "000000000001-合成重名"
    preview = build_preview(parse_excel(pack(workbook)))
    assert not preview.student_samples
    assert all("STUDENT_DUPLICATE" in row.issue_codes for row in preview.rows)


def test_issue_location_points_to_correct_row_after_pagination(workbook, pack):
    workbook["Sheet2"]["C10"] = None
    result = parse_excel(pack(workbook))
    issue = build_preview(result).issues[0]
    page = build_preview(result, row_offset=issue.row_offset, row_limit=1)
    assert issue.cell == "C10"
    assert page.rows[0].row_key == issue.row_key
    assert page.rows[0].source.score_cell == issue.cell
