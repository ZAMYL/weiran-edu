"""Teacher-only insight DTOs; no internal references or unreviewed text fields."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.importing.edu.preview import Page

from .analysis_models import AnalysisScope, DimensionCode, DisplayPolicy, FilterCondition, StatValue
from .models import Identifier, Model, StudentScore


class InsightSpec(Model):
    snapshot_id: Identifier
    conditions: Annotated[tuple[FilterCondition, ...], Field(max_length=2)] = ()
    include_dimensions: bool = True

    @field_validator("conditions", mode="before")
    @classmethod
    def json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def distinct_dimensions(self) -> "InsightSpec":
        if len({c.dimension_code for c in self.conditions}) != len(self.conditions):
            raise ValueError("DISTINCT_DIMENSIONS_REQUIRED")
        return self


class InsightQuery(InsightSpec):
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 20


class InsightRecordsQuery(InsightQuery):
    metric_key: Annotated[str, Field(min_length=1, max_length=80)]


class InsightMetric(Model):
    key: str
    value: StatValue
    unit: Literal["records", "score", "ratio"]
    denominator: int | None
    at_score: StatValue | None
    source_record_count: int


class InsightCard(Model):
    candidate_id: str
    batch_id: str
    snapshot_id: str
    candidate_type: Literal["dimension_distribution", "ability_group"]
    dimension_code: DimensionCode | None
    title: str
    summary: str
    metrics: tuple[InsightMetric, ...]
    conditions: tuple[FilterCondition, ...]
    denominator: int
    rule_id: str
    rule_version: str
    status: Literal["observed", "insufficient_data"]
    limitations: tuple[str, ...]
    suggested_next_action: str
    public_display_allowed: Literal[False] = False
    public_restrictions: tuple[str, ...]


class InsightResult(Model):
    batch_id: str
    snapshot_id: str
    audience: Literal["authorized_teacher_only"] = "authorized_teacher_only"
    scope: AnalysisScope
    conditions: tuple[FilterCondition, ...]
    include_dimensions: bool
    rule_version: str
    display_policy: DisplayPolicy = DisplayPolicy()
    comment_analysis_status: Literal["unavailable"] = "unavailable"
    comment_analysis_reason: Literal["reviewed_comment_provider_not_available"] = (
        "reviewed_comment_provider_not_available"
    )
    limitations: tuple[str, ...]
    total_count: int
    items: tuple[InsightCard, ...]
    page: Page


class InsightParticipant(Model):
    student_id: str
    display_label: str
    scores: tuple[StudentScore, ...]


class InsightRecordsResult(Model):
    batch_id: str
    snapshot_id: str
    candidate_id: str
    metric: InsightMetric
    conditions: tuple[FilterCondition, ...]
    rule_version: str
    total_count: int
    scope_record_count: int
    items: tuple[InsightParticipant, ...]
    page: Page
