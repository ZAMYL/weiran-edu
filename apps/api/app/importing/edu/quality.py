"""Full, privacy-safe quality report; counts never depend on preview pagination."""

from collections import Counter, defaultdict
from typing import Literal

from .preview import (
    PreviewIssue,
    PreviewModel,
    PreviewSummary,
    project_issues,
    summarize,
)
from .types import DIMENSIONS, PARSER_VERSION, ParsedWorkbook


class QualitySummary(PreviewSummary):
    valid_score_count: int
    missing_score_count: int
    invalid_score_count: int
    unknown_dimension_row_count: int
    affected_student_count: int
    students_with_errors: int
    students_with_warnings: int
    workbook_issue_count: int


class DimensionQuality(PreviewModel):
    dimension_code: str
    dimension_label: str
    score_row_count: int
    valid_score_count: int
    missing_score_count: int
    invalid_score_count: int
    students_missing_dimension_count: int
    students_with_duplicate_dimension_count: int


class IssueGroup(PreviewModel):
    code: str
    severity: Literal["error", "warning"]
    message: str
    occurrence_count: int
    affected_student_count: int
    affected_row_count: int
    examples: tuple[PreviewIssue, ...]


class QualityReport(PreviewModel):
    report_version: Literal["quality.v1"] = "quality.v1"
    parser_version: Literal["current_template.v1"] = PARSER_VERSION
    file_sha256: str
    status: Literal["passed", "warning", "blocked"]
    validation_passed: bool
    requires_warning_acknowledgement: bool
    summary: QualitySummary
    dimensions: tuple[DimensionQuality, ...]
    issue_groups: tuple[IssueGroup, ...]
    issues: tuple[PreviewIssue, ...]


def build_quality_report(result: ParsedWorkbook) -> QualityReport:
    """Report every issue, even when the row/issue preview displays only one page."""
    issues = project_issues(result)
    evaluations = [e for student in result.students for e in student.evaluations]
    affected = {i.preview_student_id for i in issues if i.preview_student_id}
    errors = {
        i.preview_student_id for i in issues if i.preview_student_id and i.severity == "error"
    }
    warnings = {
        i.preview_student_id for i in issues if i.preview_student_id and i.severity == "warning"
    }
    counts = [Counter(e.dimension_code for e in student.evaluations) for student in result.students]
    dimensions = []
    for spec in DIMENSIONS:
        rows = [e for e in evaluations if e.dimension_code == spec.code]
        valid = sum(e.score_units is not None for e in rows)
        missing = sum(e.score_raw is None for e in rows)
        dimensions.append(
            DimensionQuality(
                dimension_code=spec.code,
                dimension_label=spec.label,
                score_row_count=len(rows),
                valid_score_count=valid,
                missing_score_count=missing,
                invalid_score_count=len(rows) - valid - missing,
                students_missing_dimension_count=sum(c[spec.code] == 0 for c in counts),
                students_with_duplicate_dimension_count=sum(c[spec.code] > 1 for c in counts),
            )
        )
    grouped: dict[tuple[str, str], list[PreviewIssue]] = defaultdict(list)
    for issue in issues:
        grouped[issue.severity, issue.code].append(issue)
    groups = tuple(
        IssueGroup(
            severity=severity,
            code=code,
            message=items[0].message,
            occurrence_count=len(items),
            affected_student_count=len(
                {i.preview_student_id for i in items if i.preview_student_id}
            ),
            affected_row_count=len({(i.sheet_name, i.row_number) for i in items}),
            examples=tuple(items[:5]),
        )
        for (severity, code), items in sorted(grouped.items())
    )
    summary = summarize(result)
    valid = sum(e.score_units is not None for e in evaluations)
    missing = sum(e.score_raw is None for e in evaluations)
    return QualityReport(
        file_sha256=result.file_sha256,
        status="blocked"
        if summary.error_count
        else "warning"
        if summary.warning_count
        else "passed",
        validation_passed=result.can_commit,
        requires_warning_acknowledgement=summary.warning_count > 0,
        summary=QualitySummary(
            **summary.model_dump(),
            valid_score_count=valid,
            missing_score_count=missing,
            invalid_score_count=len(evaluations) - valid - missing,
            unknown_dimension_row_count=sum(e.dimension_code is None for e in evaluations),
            affected_student_count=len(affected),
            students_with_errors=len(errors),
            students_with_warnings=len(warnings),
            workbook_issue_count=sum(i.scope == "workbook" for i in issues),
        ),
        dimensions=tuple(dimensions),
        issue_groups=groups,
        issues=issues,
    )
