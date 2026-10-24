"""Public API projections only; private identity and raw text have no DTO fields."""

from datetime import date
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.importing.edu.preview import (
    Page,
    PreviewIssue,
    PreviewRow,
    PreviewSource,
    PreviewStudent,
    PreviewSummary,
)

type Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]
type Title = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
]
type Version = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True, populate_by_name=True
    )


class ImportMetadata(Model):
    data_scope_id: Identifier
    title: Title
    course: Title | None = None
    assessment_date: str | None = None
    revision_of_batch_id: Identifier | None = None

    @field_validator("assessment_date")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError("INVALID_DATE")
        return value


class ImportPatch(Model):
    preview_version: Version
    title: Title | None = None
    course: Title | None = None
    assessment_date: str | None = None

    @model_validator(mode="after")
    def valid_patch(self) -> "ImportPatch":
        supplied = self.model_fields_set - {"preview_version"}
        if not supplied or "title" in supplied and self.title is None:
            raise ValueError("INVALID_METADATA_PATCH")
        ImportMetadata.valid_date(self.assessment_date)
        return self


class CommitRequest(Model):
    preview_version: Version
    acknowledge_warnings: bool = False


class CommitResult(Model):
    import_id: str
    batch_id: str
    result: Literal["created", "duplicate", "replayed"]


class ImportInfo(Model):
    import_id: str
    status: Literal["preview_ready", "invalid", "committed", "expired"]
    data_scope_id: str
    title: str
    course: str | None
    assessment_date: str | None
    revision_of_batch_id: str | None
    file_sha256: str
    content_sha256: str | None
    content_hash_version: str
    parser_version: str
    rubric_version: str | None
    preview_version: str
    created_at: str
    expires_at: str
    committed_batch_id: str | None


class ImportView(Model):
    import_info: ImportInfo = Field(alias="import")
    preview_available: bool
    summary: PreviewSummary
    student_samples: tuple[PreviewStudent, ...] | None
    student_page: Page | None
    rows: tuple[PreviewRow, ...] | None
    row_page: Page | None
    issues: tuple[PreviewIssue, ...] | None
    issue_page: Page | None


class Batch(Model):
    batch_id: str
    data_scope_id: str
    snapshot_id: str
    title: str
    course: str | None
    assessment_date: str | None
    student_count: int
    evaluation_count: int
    rubric_version: str
    parser_version: str
    file_sha256: str
    content_sha256: str
    content_hash_version: str
    revision_of_batch_id: str | None
    is_current: bool
    created_at: str


class BatchList(Model):
    total_count: int
    items: tuple[Batch, ...]
    page: Page


class StudentScore(Model):
    dimension_code: str
    score: float
    evidence_id: str


class Student(Model):
    student_id: str
    display_label: str
    scores: tuple[StudentScore, ...]


class StudentList(Model):
    batch_id: str
    snapshot_id: str
    total_count: int
    items: tuple[Student, ...]
    page: Page


class Evidence(Model):
    evidence_id: str
    batch_id: str
    student_id: str
    dimension_code: str
    score: float
    score_raw: str
    comment: None = None
    comment_status: Literal["empty", "withheld"]
    source: PreviewSource
    rubric_criterion_id: str


class StudentEvidence(Model):
    batch_id: str
    student_id: str
    display_label: str
    rubric_version: str
    items: tuple[Evidence, ...]
