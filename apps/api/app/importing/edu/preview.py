"""Whitelist projection of a private parse result; comments require later review."""

from bisect import bisect_right
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from .types import DIMENSIONS, PARSER_VERSION, Evaluation, ParsedWorkbook

MESSAGES = {
    "STUDENT_IDENTITY_INVALID": "学生标识须为12位学号、分隔符和姓名",
    "STUDENT_DUPLICATE": "同一学号存在多个学生数据块",
    "BLOCK_HEADER_INVALID": "学生数据块表头与当前模板不一致",
    "DIMENSION_UNKNOWN": "维度为空或无法映射到当前五维编码",
    "DIMENSION_MISSING": "学生数据块缺少必需的评分维度",
    "DIMENSION_DUPLICATE": "学生数据块存在重复维度",
    "SCORE_REQUIRED": "评分不能为空",
    "SCORE_NOT_NUMERIC": "评分须使用数值单元格，不能使用文本或布尔值",
    "SCORE_INVALID": "评分不是支持的有限数值",
    "SCORE_OUT_OF_RANGE": "评分须在1至5之间",
    "SCORE_PRECISION": "评分最多保留两位小数，系统不会自动舍入",
    "COMMENT_TYPE_INVALID": "评语须为文本或空值",
    "COMMENT_EMPTY": "评语为空，请复核；此项不阻止确认",
    "COMMENT_REPEATED": "评语与其他记录完全相同，请复核；此项不阻止确认",
    "UNEXPECTED_DATA_COLUMN": "评分表出现模板之外的数据列",
    "ORPHAN_ROW": "评分行前缺少学生标识及数据块表头",
    "NO_STUDENTS": "未检测到学生数据块",
    "TITLE_ROW_INVALID": "首行只允许留空或在A列填写标题，不得填写评分数据",
    "RUBRIC_MAPPING_INVALID": "评分依据表的维度标题与固定映射不一致",
    "RUBRIC_REQUIRED": "缺少评分依据原文或模板合并区域发生变化",
}


class PreviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PreviewSource(PreviewModel):
    file_sha256: str
    sheet_name: Literal["Sheet2"] = "Sheet2"
    row_number: int
    dimension_cell: str
    score_cell: str
    comment_cell: str


class PreviewScore(PreviewModel):
    dimension_code: str | None
    score: float | None
    score_raw: str | None
    score_state: Literal["valid", "invalid", "missing"]
    comment: None = None
    comment_status: Literal["withheld", "empty"]
    source: PreviewSource


class PreviewRow(PreviewScore):
    row_key: str
    preview_student_id: str
    display_label: str
    issue_codes: tuple[str, ...]


class PreviewIssue(PreviewModel):
    issue_id: str
    severity: Literal["error", "warning"]
    code: str
    message: str
    sheet_name: Literal["Sheet1", "Sheet2"]
    row_number: int
    cell: str
    row_key: str | None = None
    row_offset: int | None = None
    preview_student_id: str | None = None
    display_label: str | None = None
    dimension_code: str | None = None
    scope: Literal["workbook", "student"]


class PreviewStudent(PreviewModel):
    preview_student_id: str
    display_label: str
    scores: tuple[PreviewScore, ...]


class Page(PreviewModel):
    offset: int
    limit: int
    returned: int
    has_more: bool


class PreviewSummary(PreviewModel):
    detected_student_count: int
    complete_student_count: int
    parsed_evaluation_count: int
    error_count: int
    warning_count: int


class ParsePreview(PreviewModel):
    """Parser-stage DTO, not the full lifecycle ImportPreview HTTP response in PR #2."""

    parser_version: Literal["current_template.v1"] = PARSER_VERSION
    file_sha256: str
    content_sha256: str | None
    content_hash_version: Literal["content.v1"] = "content.v1"
    status: Literal["preview_ready", "invalid"]
    preview_available: bool = True
    summary: PreviewSummary
    student_samples: tuple[PreviewStudent, ...]
    student_page: Page
    rows: tuple[PreviewRow, ...]
    row_page: Page
    issues: tuple[PreviewIssue, ...]
    issue_page: Page


def validate_page(offset: int, limit: int) -> None:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        raise ValueError("INVALID_PAGE")


def summarize(result: ParsedWorkbook) -> PreviewSummary:
    return PreviewSummary(
        detected_student_count=len(result.students),
        complete_student_count=sum(s.complete for s in result.students),
        parsed_evaluation_count=sum(len(s.evaluations) for s in result.students),
        error_count=sum(i.severity == "error" for i in result.issues),
        warning_count=sum(i.severity == "warning" for i in result.issues),
    )


def project_issues(result: ParsedWorkbook) -> tuple[PreviewIssue, ...]:
    """Stable within a parse result; header problems do not invent evaluation offsets."""
    students = sorted(result.students, key=lambda student: student.header_row)
    headers = [student.header_row for student in students]
    rows = [e for student in result.students for e in student.evaluations]
    offsets = {e.source.row_number: offset for offset, e in enumerate(rows)}
    dimensions = {e.source.row_number: e.dimension_code for e in rows}
    projected = []
    for index, issue in enumerate(result.issues):
        student_index = bisect_right(headers, issue.row_number) - 1
        student = (
            students[student_index] if issue.sheet_name == "Sheet2" and student_index >= 0 else None
        )
        offset = offsets.get(issue.row_number) if issue.sheet_name == "Sheet2" else None
        projected.append(
            PreviewIssue(
                issue_id=str(uuid5(UUID(result.preview_id), str(index))),
                severity=issue.severity,
                code=issue.code,
                message=MESSAGES[issue.code],
                sheet_name=issue.sheet_name,
                row_number=issue.row_number,
                cell=issue.cell,
                row_key=f"row:{issue.row_number}" if offset is not None else None,
                row_offset=offset,
                preview_student_id=student.preview_student_id if student else None,
                display_label=student.display_label if student else None,
                dimension_code=issue.dimension_code
                or (dimensions.get(issue.row_number) if issue.sheet_name == "Sheet2" else None),
                scope="student" if student else "workbook",
            )
        )
    return tuple(projected)


def _score_preview(e: Evaluation) -> PreviewScore:
    source = e.source
    return PreviewScore(
        dimension_code=e.dimension_code,
        score=e.score_units / 100 if e.score_units is not None else None,
        score_raw=str(e.score_raw) if e.score_units is not None else None,
        score_state="valid"
        if e.score_units is not None
        else ("missing" if e.score_raw is None else "invalid"),
        comment_status="withheld" if e.comment_raw and e.comment_raw.strip() else "empty",
        source=PreviewSource(
            file_sha256=source.file_sha256,
            row_number=source.row_number,
            dimension_cell=source.dimension_cell,
            score_cell=source.score_cell,
            comment_cell=source.comment_cell,
        ),
    )


def build_preview(
    result: ParsedWorkbook,
    *,
    row_offset: int = 0,
    row_limit: int = 25,
    issue_offset: int = 0,
    issue_limit: int = 50,
    student_offset: int = 0,
    student_limit: int = 20,
) -> ParsePreview:
    validate_page(row_offset, row_limit)
    validate_page(issue_offset, issue_limit)
    validate_page(student_offset, student_limit)
    evaluations = [(s, e) for s in result.students for e in s.evaluations]
    rows = []
    codes_by_row: dict[int, list[str]] = {}
    for issue in result.issues:
        if issue.sheet_name == "Sheet2":
            codes_by_row.setdefault(issue.row_number, []).append(issue.code)
    for student, e in evaluations[row_offset : row_offset + row_limit]:
        source = e.source
        rows.append(
            PreviewRow(
                row_key=f"row:{source.row_number}",
                preview_student_id=student.preview_student_id,
                display_label=student.display_label,
                **_score_preview(e).model_dump(),
                issue_codes=tuple(
                    dict.fromkeys(
                        codes_by_row.get(student.header_row, [])
                        + codes_by_row.get(source.row_number, [])
                    )
                ),
            )
        )
    issues = project_issues(result)[issue_offset : issue_offset + issue_limit]
    complete = [student for student in result.students if student.complete]
    dimension_order = {spec.code: index for index, spec in enumerate(DIMENSIONS)}
    samples = tuple(
        PreviewStudent(
            preview_student_id=student.preview_student_id,
            display_label=student.display_label,
            scores=tuple(
                _score_preview(e)
                for e in sorted(
                    student.evaluations, key=lambda e: dimension_order[e.dimension_code]
                )
            ),
        )
        for student in complete[student_offset : student_offset + student_limit]
    )
    return ParsePreview(
        file_sha256=result.file_sha256,
        content_sha256=result.content_sha256,
        status="preview_ready" if result.can_commit else "invalid",
        summary=summarize(result),
        student_samples=samples,
        student_page=Page(
            offset=student_offset,
            limit=student_limit,
            returned=len(samples),
            has_more=student_offset + len(samples) < len(complete),
        ),
        rows=tuple(rows),
        issues=issues,
        row_page=Page(
            offset=row_offset,
            limit=row_limit,
            returned=len(rows),
            has_more=row_offset + len(rows) < len(evaluations),
        ),
        issue_page=Page(
            offset=issue_offset,
            limit=issue_limit,
            returned=len(issues),
            has_more=issue_offset + len(issues) < len(result.issues),
        ),
    )
