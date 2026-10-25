"""Stage-one bridge DTOs; production discovery routes are deliberately not registered."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.importing.edu.preview import Page

from .analysis_models import FilterCondition, Statistics
from .insight_models import InsightParticipant
from .models import Identifier, Model

PROFILE = "edu.discovery.profile.v1"


class DiscoverySpec(Model):
    snapshot_id: Identifier
    scope_conditions: Annotated[tuple[FilterCondition, ...], Field(max_length=2)] = ()
    profile: Literal["edu.discovery.profile.v1"] = PROFILE

    @field_validator("scope_conditions", mode="before")
    @classmethod
    def json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def distinct_dimensions(self) -> "DiscoverySpec":
        if len({c.dimension_code for c in self.scope_conditions}) != len(self.scope_conditions):
            raise ValueError("DISTINCT_DIMENSIONS_REQUIRED")
        return self


class DiscoveryQuery(DiscoverySpec):
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=50)] = 20


class DiscoveryRecordsQuery(DiscoveryQuery):
    metric_key: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")]


class DiscoveryScope(Model):
    batch_id: str
    snapshot_id: str
    input_key: str
    profile: str = PROFILE
    scope_conditions: tuple[FilterCondition, ...]
    batch_record_count: int
    scope_record_count: int
    eligible_count: int
    unevaluable_count: int
    statistics: Statistics
    public_display_allowed: Literal[False] = False


class DiscoverySummary(Model):
    discovery_id: Annotated[str, Field(pattern=r"^[a-f0-9]{24}$")]
    batch_id: str
    snapshot_id: str
    input_key: str
    rule_version: Annotated[str, Field(min_length=1, max_length=80)]
    type: Literal[
        "dimension_relation", "group_difference", "rare_combination", "boundary_sensitivity"
    ]
    title: Annotated[str, Field(min_length=1, max_length=160)]
    summary: Annotated[str, Field(max_length=1000)]
    status: Literal["displayable", "interpret_cautiously", "insufficient_evidence"]
    public_display_allowed: Literal[False] = False


class DiscoveryList(Model):
    scope: DiscoveryScope
    rule_version: str
    qualified_count: int
    retained_count: int
    truncated: bool
    items: tuple[DiscoverySummary, ...]
    page: Page


class DiscoveryParticipants(Model):
    scope: DiscoveryScope
    metric_key: str
    total_count: int
    items: tuple[InsightParticipant, ...]
    page: Page
