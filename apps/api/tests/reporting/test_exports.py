"""Synthetic export round-trip, privacy, precision and resource acceptance."""

from dataclasses import replace
from datetime import timedelta
from fractions import Fraction
from io import BytesIO
from zipfile import ZipFile

import pytest
from openpyxl import Workbook, load_workbook

from app.reporting.edu import (
    DetailAuthorization,
    ExportLimits,
    ReportError,
    Source,
    generate_report,
)
from app.reporting.edu.demo import synthetic_authorization, synthetic_report
from app.reporting.edu.render import md_text, text_cell


def all_text(data):
    with ZipFile(BytesIO(data)) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xml")
        )


@pytest.mark.parametrize("size", [0, 1, 17, 53, 105, 320, 2000])
def test_scale_reports_come_from_actual_statistics(size):
    report = synthetic_report(size)
    result = generate_report(report)
    workbook = load_workbook(BytesIO(result.xlsx), data_only=False)
    assert workbook.sheetnames == ["报告说明", "维度统计", "群体结果", "洞察事实"]
    info = {
        row[0]: row[1] for row in workbook["报告说明"].iter_rows(values_only=True) if row and row[0]
    }
    assert info["测评记录数"] == size
    assert info["统计项目数"] == len(report.dimensions)
    assert "合成演示" in result.markdown
    assert "synthetic-member-" not in all_text(result.xlsx)
    assert "synthetic-evidence-" not in result.markdown
    for sheet in workbook:
        assert all(
            cell.data_type != "f" and cell.hyperlink is None for row in sheet for cell in row
        )
    workbook.close()


@pytest.mark.parametrize("quantity", [0, 1, 3, 7])
def test_dynamic_statistics_items_do_not_change_upload_scheme(quantity):
    report = synthetic_report(17)
    source = list(report.dimensions)
    while len(source) < quantity:
        source.append(replace(source[-1], code=f"custom_item_{len(source)}"))
    report = replace(report, dimensions=tuple(source[:quantity]), groups=())
    result = generate_report(report)
    workbook = load_workbook(BytesIO(result.xlsx))
    means = [
        row
        for row in workbook["维度统计"].iter_rows(values_only=True)
        if len(row) > 2 and row[2] == "均值"
    ]
    assert len(means) == quantity
    assert f"统计项目 {quantity} 项" in result.markdown
    workbook.close()


def test_fraction_denominator_is_not_headcount_and_missing_is_not_zero():
    report = synthetic_report(4)
    result = generate_report(report)
    workbook = load_workbook(BytesIO(result.xlsx))
    row = next(
        row
        for row in workbook["维度统计"].iter_rows(values_only=True)
        if row[0] == "分析范围" and row[1] == "算法与数据结构" and row[2] == "均值"
    )
    assert row[3:7] == (2.9375, "2.94", "47/16", 4)
    empty = generate_report(synthetic_report(0))
    assert "暂无有效数据" in empty.markdown
    assert "没有匹配记录" in empty.markdown
    missing = replace(
        report.dimensions[0],
        valid_count=0,
        missing_count=3,
        invalid_count=1,
        mean=None,
        median=None,
        minimum=None,
        maximum=None,
        distribution=(),
    )
    data = generate_report(replace(report, dimensions=(missing,), groups=()))
    wb = load_workbook(BytesIO(data.xlsx))
    mean = next(row for row in wb["维度统计"].iter_rows(values_only=True) if row[2] == "均值")
    assert mean[3] is None and mean[4] == "暂无有效数据" and mean[6] == 0
    workbook.close()
    wb.close()


def test_anonymous_details_need_exact_upstream_grant_and_stay_complete():
    report = synthetic_report(17)
    with pytest.raises(ReportError, match="detail_authorization_required"):
        generate_report(report, include_details=True)
    grant = synthetic_authorization(report)
    result = generate_report(report, include_details=True, authorization=grant)
    workbook = load_workbook(BytesIO(result.xlsx))
    cells = list(workbook["群体结果"].iter_rows(values_only=True))
    member_rows = [
        row for row in cells if isinstance(row[1], str) and row[1].startswith("synthetic-member-")
    ]
    assert len(member_rows) == report.groups[0].matched_count * len(report.dimensions)
    assert {row[1] for row in member_rows} == grant.grants[0].student_ids
    assert all(row[3] in (2, 2.75, 3, 4, 5) for row in member_rows)
    assert "synthetic-member-" in result.markdown
    assert "非公开" in result.markdown
    denied = replace(grant, grants=(replace(grant.grants[0], student_ids=frozenset()),))
    with pytest.raises(ReportError, match="group_access_not_authorized"):
        generate_report(report, include_details=True, authorization=denied)
    workbook.close()


def test_missing_group_members_and_foreign_authority_are_rejected():
    report = synthetic_report(17)
    missing = replace(report, groups=(replace(report.groups[0], members=None),))
    with pytest.raises(ReportError, match="incomplete_group_members"):
        generate_report(
            missing, include_details=True, authorization=synthetic_authorization(report)
        )
    wrong = DetailAuthorization(Source("other-batch", report.source.snapshot_id), ())
    with pytest.raises(ReportError, match="source_mismatch"):
        generate_report(report, include_details=True, authorization=wrong)


@pytest.mark.parametrize(
    "attack",
    [
        "=1+1",
        "+SUM(1,1)",
        "-1+2",
        "@SUM(A1:A2)",
        "\t=cmd|' /C calc'!A0",
        '\n=HYPERLINK("https://example.invalid","x")',
        "\r\n=1+1",
        "https://example.invalid",
        "<script>alert(1)</script>",
    ],
)
def test_untrusted_cell_text_cannot_become_formula_or_external_link(attack):
    workbook = Workbook()
    text_cell(workbook.active["A1"], attack)
    stream = BytesIO()
    workbook.save(stream)
    reopened = load_workbook(BytesIO(stream.getvalue()), data_only=False)
    assert reopened.active["A1"].data_type == "s"
    assert reopened.active["A1"].value == attack.replace("\r\n", "\n").replace("\r", "\n")
    assert reopened.active["A1"].hyperlink is None
    with ZipFile(BytesIO(stream.getvalue())) as archive:
        assert not any("externalLinks" in name for name in archive.namelist())
        assert all(
            b'TargetMode="External"' not in archive.read(name)
            for name in archive.namelist()
            if name.endswith(".rels")
        )
        assert b"<f>" not in archive.read("xl/worksheets/sheet1.xml")
    assert "<script>" not in md_text(attack)
    reopened.close()
    workbook.close()


def test_same_input_has_stable_files_and_editable_workbook():
    report = synthetic_report(17)
    first = generate_report(report)
    second = generate_report(report)
    assert first == second
    later = generate_report(replace(report, generated_at=report.generated_at + timedelta(days=1)))
    assert later != first
    workbook = load_workbook(BytesIO(first.xlsx))
    workbook["报告说明"]["B5"] = "edited-synthetic-value"
    stream = BytesIO()
    workbook.save(stream)
    edited = load_workbook(BytesIO(stream.getvalue()))
    assert edited["报告说明"]["B5"].value == "edited-synthetic-value"
    workbook.close()
    edited.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: replace(r, source=Source("different-batch", r.source.snapshot_id)),
        lambda r: replace(r, dimensions=(replace(r.dimensions[0], valid_count=99),)),
        lambda r: replace(r, dimensions=(replace(r.dimensions[0], mean=Fraction(1)),)),
        lambda r: replace(r, dimensions=(r.dimensions[0], r.dimensions[0])),
        lambda r: replace(r, groups=(replace(r.groups[0], ratio=Fraction(1)),)),
        lambda r: replace(r, groups=(replace(r.groups[0], members=r.groups[0].members[:1]),)),
        lambda r: replace(
            r,
            groups=(
                replace(r.groups[0], members=(r.groups[0].members[0],) * r.groups[0].matched_count),
            ),
        ),
        lambda r: replace(r, generated_at=r.generated_at.replace(tzinfo=None)),
    ],
)
def test_invalid_report_never_yields_partial_export(mutation):
    with pytest.raises(ReportError):
        generate_report(mutation(synthetic_report(17)))


@pytest.mark.parametrize(
    "limits",
    [
        ExportLimits(max_rows_per_sheet=5),
        ExportLimits(max_total_cells=10),
        ExportLimits(max_file_bytes=10),
        ExportLimits(max_rows_per_sheet=0),
    ],
)
def test_resource_limits_fail_before_returning_any_file(limits):
    with pytest.raises(ReportError):
        generate_report(synthetic_report(17), limits=limits)


def test_unavailable_rubric_and_different_scales_are_explicit():
    report = synthetic_report(4)
    no_scale = replace(report, dimensions=(replace(report.dimensions[0], scale=None),), groups=())
    result = generate_report(no_scale)
    assert "未提供评分标准和依据版本" in result.markdown
    changed = replace(report.dimensions[0].scale, minimum=Fraction(0), maximum=Fraction(10))
    mixed = replace(
        report,
        dimensions=(replace(report.dimensions[0], scale=changed), report.dimensions[1]),
        groups=(),
    )
    assert "不同量表不默认可比" in generate_report(mixed).markdown


def test_synthetic_flag_is_not_an_authorization_or_safe_identifier_bypass():
    report = synthetic_report(17)
    with pytest.raises(ReportError):
        generate_report(report, include_details=True)
    with pytest.raises(ReportError):
        generate_report(replace(report, source=Source("wedu_not-a-real-secret", "snapshot")))
    with pytest.raises(ReportError):
        generate_report(replace(report, source=Source("202600000001", "snapshot")))


def test_cell_budget_counts_titles_and_formatted_empty_cells():
    from xml.etree import ElementTree

    report = synthetic_report(4)
    result = generate_report(report)
    with ZipFile(BytesIO(result.xlsx)) as archive:
        cell_count = sum(
            len(
                ElementTree.fromstring(archive.read(name)).findall(
                    ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"
                )
            )
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
    assert generate_report(report, limits=ExportLimits(max_total_cells=cell_count))
    with pytest.raises(ReportError, match="report_layout_limit"):
        generate_report(report, limits=ExportLimits(max_total_cells=cell_count - 1))


def test_detail_scores_use_declared_scale_precision():
    report = synthetic_report(4)
    output = generate_report(
        report, include_details=True, authorization=synthetic_authorization(report)
    )
    workbook = load_workbook(BytesIO(output.xlsx))
    cells = [
        row[3]
        for row in workbook["群体结果"]
        if isinstance(row[1].value, str) and row[1].value.startswith("synthetic-member-")
    ]
    assert cells and all(cell.number_format == "0.00" for cell in cells)
    workbook.close()
