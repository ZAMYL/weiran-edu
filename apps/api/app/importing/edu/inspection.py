"""One parse yields consistent public preview and a complete quality report."""

from .parser import parse_excel
from .preview import ParsePreview, PreviewModel, build_preview, validate_page
from .quality import QualityReport, build_quality_report
from .types import ParseLimits


class ExcelInspection(PreviewModel):
    preview: ParsePreview
    quality_report: QualityReport


def inspect_excel(
    data: bytes,
    *,
    limits: ParseLimits | None = None,
    row_offset: int = 0,
    row_limit: int = 25,
    student_offset: int = 0,
    student_limit: int = 20,
    issue_offset: int = 0,
    issue_limit: int = 50,
) -> ExcelInspection:
    for offset, limit in (
        (row_offset, row_limit),
        (student_offset, student_limit),
        (issue_offset, issue_limit),
    ):
        validate_page(offset, limit)
    result = parse_excel(data, limits=limits)
    return ExcelInspection(
        preview=build_preview(
            result,
            row_offset=row_offset,
            row_limit=row_limit,
            student_offset=student_offset,
            student_limit=student_limit,
            issue_offset=issue_offset,
            issue_limit=issue_limit,
        ),
        quality_report=build_quality_report(result),
    )
