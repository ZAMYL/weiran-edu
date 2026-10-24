from app.importing.edu import build_quality_report, parse_excel
from app.importing.edu.report import render_quality_report


def test_readable_report_has_counts_source_locations_and_no_private_values(workbook, pack):
    workbook["Sheet2"]["D3"] = "隐藏合成评语和合成联系方式"
    workbook["Sheet2"]["C4"] = "私密非法评分"
    report = build_quality_report(parse_excel(pack(workbook)))
    text = render_quality_report(report)
    assert "存在阻断错误" in text
    assert "| 评分行 | 10 |" in text
    assert "SCORE_NOT_NUMERIC" in text and "C4" in text and "学生 001" in text
    for private in ("隐藏合成评语", "私密非法评分", "000000000001", "合成同学0"):
        assert private not in text


def test_passed_report_is_readable_without_issues(workbook, pack):
    text = render_quality_report(build_quality_report(parse_excel(pack(workbook))))
    assert "解析结果：**通过**" in text
    assert "未发现阻断错误或待复核警告" in text
