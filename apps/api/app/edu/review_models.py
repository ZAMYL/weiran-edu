"""Versioned review projections, separate from immutable assessment facts."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.importing.edu.preview import Page

from .analysis_models import Statistics, StatValue
from .discovery_models import DiscoveryDetail, DiscoveryScope, DiscoverySpec
from .insight_models import InsightCard, InsightSpec
from .models import Identifier, Model, Student, Title

type TextKind = Literal["comment", "rubric"]
type DisplayText = Annotated[str, Field(min_length=1, max_length=4000)]
type Note = Annotated[str, Field(max_length=2000)]
type PositiveVersion = Annotated[int, Field(ge=1)]


class TextReviewWrite(Model):
    snapshot_id: Identifier
    expected_version: Annotated[int, Field(ge=0)]
    status: Literal["published", "withheld"]
    display_text: DisplayText | None = None

    @model_validator(mode="after")
    def publication_text(self) -> "TextReviewWrite":
        if (self.status == "published") != (self.display_text is not None):
            raise ValueError("PUBLICATION_TEXT_REQUIRED")
        return self


class TextReview(Model):
    batch_id: str
    snapshot_id: str
    target_kind: TextKind
    target_id: str
    version: int
    status: Literal["empty", "withheld", "published"]
    display_text: str | None
    review_id: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None


class TextReviewPreview(TextReview):
    preview_text: str | None
    preview_truncated: bool
    notice: str = "仅提供自动脱敏预览；发布前仍须教师人工核对，不代表公开发布许可。"


class DiscoverySelection(Model):
    input_key: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    discovery_ids: Annotated[tuple[Identifier, ...], Field(max_length=3)]

    @field_validator("discovery_ids", mode="before")
    @classmethod
    def json_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique_ids(self) -> "DiscoverySelection":
        if len(set(self.discovery_ids)) != len(self.discovery_ids):
            raise ValueError("DUPLICATE_DISCOVERY")
        return self


class AnalysisCreate(InsightSpec):
    title: Title
    note: Note = ""
    candidate_ids: Annotated[tuple[Identifier, ...], Field(max_length=6)] | None = None
    discovery: DiscoverySelection | None = None

    @field_validator("candidate_ids", mode="before")
    @classmethod
    def json_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique_ids(self) -> "AnalysisCreate":
        if self.discovery is not None and (self.include_dimensions or self.candidate_ids):
            raise ValueError("DISCOVERY_SELECTION_REQUIRES_SEPARATE_CANDIDATES")
        if self.candidate_ids is not None and len(set(self.candidate_ids)) != len(
            self.candidate_ids
        ):
            raise ValueError("DUPLICATE_CANDIDATE")
        return self


class FindingDecision(Model):
    candidate_id: Identifier
    status: Literal["pending", "confirmed", "dismissed"]
    note: Note = ""


class AnalysisPatch(Model):
    snapshot_id: Identifier
    expected_version: PositiveVersion
    title: Title | None = None
    note: Note | None = None
    decisions: Annotated[tuple[FindingDecision, ...], Field(max_length=6)] = ()

    @field_validator("decisions", mode="before")
    @classmethod
    def json_decisions(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def supplied_patch(self) -> "AnalysisPatch":
        if not self.model_fields_set - {"snapshot_id", "expected_version"}:
            raise ValueError("EMPTY_PATCH")
        if any(
            name in self.model_fields_set and getattr(self, name) is None
            for name in ("title", "note")
        ):
            raise ValueError("NULL_PATCH")
        if len({d.candidate_id for d in self.decisions}) != len(self.decisions):
            raise ValueError("DUPLICATE_DECISION")
        return self


class SavedDiscoverySnapshot(Model):
    spec: DiscoverySpec
    scope: DiscoveryScope
    findings: tuple[DiscoveryDetail, ...]


class AnalysisSnapshot(Model):
    query: InsightSpec
    statistics: Statistics
    group_statistics: Statistics | None
    total_count: int
    matched_count: int
    ratio: StatValue
    members: tuple[Student, ...]
    candidates: tuple[InsightCard, ...]
    discovery: SavedDiscoverySnapshot | None = Field(default=None, exclude_if=lambda v: v is None)


class SavedFinding(Model):
    candidate: InsightCard
    teacher_review: FindingDecision


class SavedDiscoveryFinding(Model):
    discovery: DiscoveryDetail
    teacher_review: FindingDecision


class AnalysisSummary(Model):
    result_id: str
    batch_id: str
    snapshot_id: str
    version: int
    title: str
    created_at: str
    updated_at: str


class AnalysisResult(AnalysisSummary):
    audience: Literal["authorized_teacher_only"] = "authorized_teacher_only"
    note: str
    reviewed_by: str
    analysis: AnalysisSnapshot
    findings: tuple[SavedFinding, ...]
    discovery_findings: tuple[SavedDiscoveryFinding, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    notice: str = "系统候选与教师判断分别保存；教师确认不等于因果证明或公开发布许可。"


class AnalysisList(Model):
    batch_id: str
    snapshot_id: str
    total_count: int
    items: tuple[AnalysisSummary, ...]
    page: Page
