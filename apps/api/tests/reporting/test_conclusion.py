"""The exported report must open with a sentence set, never a bare table."""

from app.reporting.edu.demo import synthetic_report
from app.reporting.edu.layout import conclusion, plan
from app.reporting.edu.models import ExportLimits


def test_conclusion_names_extremes_and_data_quality():
    headline, points = conclusion(synthetic_report(17))
    assert "均值最低" in headline and "最高" in headline
    assert any(point.startswith("数据质量：") for point in points)


def test_report_plan_starts_with_the_conclusion_section():
    sheets = plan(synthetic_report(17), False, ExportLimits())
    info = next(sheet for sheet in sheets if sheet.name == "测评概况")
    values = [cell.value for row in info.rows for cell in row.cells]
    assert "本次结论" in values
    assert any(isinstance(value, str) and "均值最低" in value for value in values)
