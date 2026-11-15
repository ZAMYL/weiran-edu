"""Synthetic teacher-body, audit, numeric-format and editing acceptance."""

import re
from dataclasses import replace
from fractions import Fraction
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.reporting.edu import Frequency, generate_report
from app.reporting.edu.demo import synthetic_report
from tests.reporting.test_reviewed import attach, saved_inputs


def section_text(workbook, *, technical=False):
    return "\n".join(
        str(cell.value)
        for sheet in workbook
        if (sheet.title == "技术附录") == technical
        for row in sheet
        for cell in row
        if cell.value is not None
    )


@pytest.mark.parametrize("size", [17, 53, 105])
def test_teacher_body_and_audit_reconcile_without_debug_fields(size):
    report, saved = saved_inputs(size)
    report = attach(report, saved)
    before = repr(report)
    files = generate_report(report)
    body, audit = files.markdown.split("## 技术附录", 1)
    assert repr(report) == before
    workbook = load_workbook(BytesIO(files.xlsx), data_only=False)
    book_body = section_text(workbook)
    book_audit = section_text(workbook, technical=True)
    for text in (body, book_body):
        for hidden in (
            report.source.batch_id,
            report.source.snapshot_id,
            saved.result_id,
            report.rule_version,
            "eligible_count",
            "algorithm_data_structure",
            "pending",
            "dismissed",
            "withheld",
            "insufficient_data",
            "AND",
            "不可用",
            "待审核",
            "规则版本",
            "精确值",
        ):
            assert hidden not in text
        assert not re.search(r"\d+/\d+", text)
        assert "缺失评分人数" not in text and "无效评分人数" not in text
        assert "不可判定人数" not in text
        assert "教师已确认" in text
    for text in (audit, book_audit):
        assert report.source.batch_id in text and report.source.snapshot_id in text
        assert saved.result_id in text and report.rule_version in text
        assert all(f.candidate_id in text for f in report.reviewed.findings)
    # Hand-calculated full-batch mean, independent of report display and saved DTOs.
    values = (Fraction(2), Fraction(11, 4), Fraction(3), Fraction(4))
    expected = sum((values[i % 4] for i in range(size)), Fraction()) / size
    summary = next(row for row in workbook["维度统计"] if row[0].value == "算法与数据结构")
    assert summary[1].value == size and summary[1].number_format == "#,##0"
    assert summary[2].value == pytest.approx(float(expected))
    assert summary[2].number_format == "0.00"
    assert f"{expected.numerator}/{expected.denominator}" in audit
    group_values = {row[0].value: row[1] for row in workbook["选定群体"] if len(row) > 1}
    matched = sum(i % 4 in (0, 1) for i in range(size))
    assert group_values["符合条件人数"].value == matched
    assert group_values["符合条件人数"].number_format == "#,##0"
    assert group_values["占全部测评人数比例"].value == pytest.approx(matched / size)
    assert group_values["占全部测评人数比例"].number_format == "0.00%"
    facts = workbook["教师确认发现"]
    assert tuple(cell.value for cell in facts[5]) == ("发现", "关键结果", "评分依据状态")
    assert facts["B6"].value and not facts.row_dimensions[6].hidden
    assert facts.row_dimensions[6].collapsed and facts.row_dimensions[7].outlineLevel == 1
    assert facts.row_dimensions[7].hidden
    audit_sheet = workbook["技术附录"]
    assert audit_sheet.sheet_properties.outlinePr.summaryBelow is False
    assert any(
        audit_sheet.row_dimensions[cell.row].collapsed
        for cell in audit_sheet["A"]
        if cell.value == "维度精确统计"
    )
    assert all(
        cell.data_type != "f" and cell.hyperlink is None
        for sheet in workbook
        for row in sheet
        for cell in row
    )
    assert "<details>" in audit and "</details>" in audit
    # The report is editable; editing the file cannot mutate the original platform facts.
    workbook["测评概况"]["B5"] = "教师本地补充"
    stream = BytesIO()
    workbook.save(stream)
    reopened = load_workbook(BytesIO(stream.getvalue()))
    assert reopened["测评概况"]["B5"].value == "教师本地补充"
    assert summary[2].value == pytest.approx(float(expected))
    reopened.close()
    workbook.close()


def test_rounding_is_display_only_even_at_half_cent_boundary():
    report = synthetic_report(1)
    value = Fraction(107, 40)  # 2.675: display 2.68, never replace input with 2.68.
    dimension = replace(
        report.dimensions[0],
        mean=value,
        median=value,
        minimum=value,
        maximum=value,
        distribution=(Frequency(value, 1),),
        scale=replace(report.dimensions[0].scale, step=Fraction(1, 1000)),
    )
    report = replace(report, dimensions=(dimension,), groups=())
    files = generate_report(report)
    body, audit = files.markdown.split("## 技术附录", 1)
    assert "2.68" in body and "107/40" in audit
    assert report.dimensions[0].mean == value
    workbook = load_workbook(BytesIO(files.xlsx))
    assert workbook["维度统计"]["C5"].value == 2.675
    assert workbook["维度统计"]["C5"].number_format == "0.00"
    workbook.close()


def test_nonzero_quality_issues_are_visible_and_not_converted_to_zero_scores():
    report = synthetic_report(4)
    dimension = replace(
        report.dimensions[0],
        valid_count=2,
        missing_count=1,
        invalid_count=1,
        mean=Fraction(2),
        median=Fraction(2),
        minimum=Fraction(1),
        maximum=Fraction(3),
        distribution=(Frequency(Fraction(1), 1), Frequency(Fraction(3), 1)),
    )
    files = generate_report(replace(report, dimensions=(dimension,), groups=()))
    body = files.markdown.split("## 技术附录", 1)[0]
    assert "评分质量说明" in body and "缺失人数" in body and "无效人数" in body
    assert "有效样本 2 人，共 4 人" in body
    workbook = load_workbook(BytesIO(files.xlsx))
    row = next(row for row in workbook["维度统计"] if row[0].value == "算法与数据结构")
    assert row[1].value == 2 and row[2].value == 2  # Not the incorrect 4-student mean of 1.
    assert [cell.value for cell in row[6:8]] == [1, 1]
    table = workbook["维度统计"]
    assert [table[f"{column}7"].value for column in "BCD"] == [
        "评分（分）",
        "人数",
        "占有效评分比例",
    ]
    assert [table[f"{column}8"].value for column in "BCD"] == [1, 1, 0.5]
    assert [table[f"{column}9"].value for column in "BCD"] == [3, 1, 0.5]
    assert table["D8"].number_format == "0.0%"
    assert len(table.conditional_formatting) == 1
    workbook.close()


def test_unevaluable_group_records_keep_both_denominators_visible():
    report = synthetic_report(4)
    group = replace(
        report.groups[0], eligible_count=2, unevaluable_count=2, eligible_ratio=Fraction(1)
    )
    files = generate_report(replace(report, groups=(group,)))
    body = files.markdown.split("## 技术附录", 1)[0]
    assert "不可判定人数 | 2" in body
    assert "50.00%" in body and "100.00%" in body
    assert "仍保留在全部测评人数中" in body


@pytest.mark.parametrize("status", ["pending", "dismissed"])
def test_empty_discovery_sections_and_unconfirmed_references_are_omitted(status):
    report, saved = saved_inputs(statuses=(status,) * 6, filtered=False)
    files = generate_report(attach(report, saved))
    assert "## 教师确认发现" not in files.markdown
    assert "## 选定群体" not in files.markdown
    assert "不可用" not in files.markdown and "待审核" not in files.markdown
    assert all(f.candidate.candidate_id not in files.markdown for f in saved.findings)
    workbook = load_workbook(BytesIO(files.xlsx))
    assert workbook.sheetnames == ["测评概况", "维度统计", "技术附录"]
    workbook.close()
