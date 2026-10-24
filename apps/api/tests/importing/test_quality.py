import pytest

from app.importing.edu import build_preview, build_quality_report, inspect_excel, parse_excel
from app.importing.edu.types import DIMENSIONS


def test_clean_report_counts_every_dimension(workbook, pack):
    report = build_quality_report(parse_excel(pack(workbook)))
    assert report.status == "passed"
    assert report.validation_passed
    assert not report.requires_warning_acknowledgement
    assert report.summary.valid_score_count == 10
    assert report.summary.invalid_score_count == report.summary.missing_score_count == 0
    assert report.summary.affected_student_count == 0
    assert report.issues == report.issue_groups == ()
    assert [d.dimension_code for d in report.dimensions] == [d.code for d in DIMENSIONS]
    assert all(d.valid_score_count == d.score_row_count == 2 for d in report.dimensions)


def test_warning_report_is_complete_beyond_preview_page(workbook, pack):
    sheet = workbook["Sheet2"]
    for row in range(3, 8):
        sheet.cell(row, 4).value = "完全相同的合成观察"
    result = parse_excel(pack(workbook))
    preview = build_preview(result, row_limit=1, issue_limit=1)
    report = build_quality_report(result)
    assert len(preview.issues) == 1 and preview.issue_page.has_more
    assert report.status == "warning" and report.validation_passed
    assert report.requires_warning_acknowledgement
    assert len(report.issues) == report.summary.warning_count == 5
    group = report.issue_groups[0]
    assert group.code == "COMMENT_REPEATED"
    assert group.occurrence_count == group.affected_row_count == 5
    assert group.affected_student_count == 1
    assert report.summary.students_with_warnings == report.summary.affected_student_count == 1


def test_large_report_includes_all_issues_and_limits_only_group_examples(workbook, pack):
    sheet = workbook["Sheet2"]
    for index in range(12):
        header = 2 + index * 6
        sheet.cell(header, 1).value = f"{index + 1:012d} 合成同学"
        for col, value in enumerate(("维度", "评分（1~5）", "评语"), 2):
            sheet.cell(header, col).value = value
        for offset, spec in enumerate(DIMENSIONS, 1):
            sheet.cell(header + offset, 2).value = spec.label
            sheet.cell(header + offset, 3).value = 3
            sheet.cell(header + offset, 4).value = "全量重复合成评语"
    result = parse_excel(pack(workbook))
    preview = build_preview(result)
    report = build_quality_report(result)
    assert len(preview.issues) == 50 and preview.issue_page.has_more
    assert len(report.issues) == report.summary.warning_count == 60
    assert report.issue_groups[0].occurrence_count == 60
    assert len(report.issue_groups[0].examples) == 5
    assert report.summary.students_with_warnings == 12


def test_blocked_report_distinguishes_missing_invalid_and_absent_dimension(workbook, pack):
    sheet = workbook["Sheet2"]
    sheet["C3"] = None
    sheet["C4"] = "隐藏合成文字"
    sheet.delete_rows(7)
    result = parse_excel(pack(workbook))
    report = build_quality_report(result)
    assert report.status == "blocked" and not report.validation_passed
    assert report.summary.parsed_evaluation_count == 9
    assert report.summary.valid_score_count == 7
    assert report.summary.missing_score_count == report.summary.invalid_score_count == 1
    assert report.summary.students_with_errors == report.summary.affected_student_count == 1
    algorithm, implementation, _, _, strategy = report.dimensions
    assert algorithm.missing_score_count == 1
    assert implementation.invalid_score_count == 1
    assert strategy.students_missing_dimension_count == 1
    assert strategy.missing_score_count == 0  # Absent row differs from a row with no score.
    missing = next(i for i in report.issues if i.code == "DIMENSION_MISSING")
    assert missing.dimension_code == "solution_strategy"
    assert missing.preview_student_id == result.students[0].preview_student_id
    assert missing.cell == "B2"
    assert missing.row_key is None and missing.row_offset is None
    score = next(i for i in report.issues if i.code == "SCORE_REQUIRED")
    assert score.cell == "C3" and score.row_key == "row:3" and score.row_offset == 0
    assert score.dimension_code == "algorithm_data_structure"


def test_duplicate_dimensions_and_unknown_rows_remain_visible(workbook, pack):
    workbook["Sheet2"]["B3"] = "代码实现能力"
    workbook["Sheet2"]["B5"] = "未知维度私密文本"
    report = build_quality_report(parse_excel(pack(workbook)))
    assert report.summary.unknown_dimension_row_count == 1
    assert report.dimensions[0].students_missing_dimension_count == 1
    assert report.dimensions[1].students_with_duplicate_dimension_count == 1
    assert report.summary.valid_score_count == 10  # Scalar scores; dimensions fail separately.
    assert "未知维度私密文本" not in report.model_dump_json()


def test_workbook_errors_do_not_invent_student_or_evaluation_locations(workbook, pack):
    workbook["Sheet1"]["B2"] = "私密错误标题"
    result = parse_excel(pack(workbook))
    report = build_quality_report(result)
    assert report.status == "blocked"
    assert report.summary.workbook_issue_count == 1
    assert report.summary.affected_student_count == 0
    issue = report.issues[0]
    assert issue.scope == "workbook" and issue.cell == "B2"
    assert issue.preview_student_id is None and issue.row_offset is None
    assert "私密错误标题" not in report.model_dump_json()


def test_inspection_has_matching_ids_counts_and_issues(workbook, pack):
    workbook["Sheet2"]["C3"] = None
    inspection = inspect_excel(pack(workbook), row_limit=1, issue_limit=1)
    assert inspection.preview.file_sha256 == inspection.quality_report.file_sha256
    assert inspection.preview.summary.error_count == inspection.quality_report.summary.error_count
    issue = inspection.preview.issues[0]
    assert issue in inspection.quality_report.issues
    assert issue.preview_student_id == inspection.preview.rows[0].preview_student_id


def test_issue_ids_are_stable_within_one_result_and_renew_on_reparse(workbook, pack):
    workbook["Sheet2"]["C3"] = None
    data = pack(workbook)
    result = parse_excel(data)
    preview = build_preview(result)
    assert preview.issues == build_preview(result).issues == build_quality_report(result).issues
    assert preview.issues[0].issue_id != build_preview(parse_excel(data)).issues[0].issue_id


def test_report_does_not_expose_private_data_even_for_invalid_input(workbook, pack, caplog):
    sheet = workbook["Sheet2"]
    sheet["A2"] = "无效身份_私密姓名_000000000001"
    sheet["C3"] = 19900000000
    sheet["D3"] = "私密评语_合成电话_19900000000"
    result = parse_excel(pack(workbook))
    report = build_quality_report(result)
    output = report.model_dump_json()
    for secret in ("无效身份", "私密姓名", "000000000001", "19900000000", "私密评语"):
        assert secret not in output
    assert not caplog.records
    assert report.summary.error_count == 2


@pytest.mark.parametrize(
    "offset,limit", [(-1, 20), (0, 0), (0, 101), (True, 1), (0, False), (1.2, 5)]
)
def test_invalid_page_is_rejected_before_parsing(offset, limit):
    with pytest.raises(ValueError, match="INVALID_PAGE"):
        inspect_excel(b"not a workbook", student_offset=offset, student_limit=limit)
