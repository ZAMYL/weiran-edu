"""Authorized discovery inputs and whitelisted projections of the kernel chart contract."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.core.exact_json import DiscoveryDisplay, ExactDenominator, ExactNumerator
from app.importing.edu.preview import Page

from .analysis_models import Comparison, DimensionCode, FilterCondition, Statistics
from .insight_models import InsightParticipant
from .models import Identifier, Model

PROFILE = "edu.discovery.profile.v1"
DISCOVERY_RETENTION_LIMIT = 50


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


class DiscoveryExact(Model):
    numerator: ExactNumerator
    denominator: Annotated[ExactDenominator, Field(gt=0)]
    display: DiscoveryDisplay


class DiscoveryApproximate(Model):
    value: Annotated[float, Field(allow_inf_nan=False)]
    method: Literal["stdlib_float"]


class DiscoveryCondition(Model):
    dimension_code: DimensionCode
    operator: Comparison
    threshold: DiscoveryExact


class RelationMetrics(Model):
    left: DimensionCode
    right: DimensionCode
    rho: DiscoveryApproximate | None
    paired_count: int
    excluded_count: int
    student_denominator: int


class GroupMetrics(Model):
    outcome_dimension: DimensionCode
    target_count: int
    remainder_count: int
    unevaluable_count: int
    student_denominator: int
    eligible_denominator: int
    share_total: DiscoveryExact | None
    share_eligible: DiscoveryExact | None


class RareMetrics(Model):
    left: DimensionCode
    right: DimensionCode
    pair_count: int
    excluded_count: int
    match_count: int
    pattern: Literal["individual_review", "small_group_review", "group_pattern"]
    share_pairs: DiscoveryExact
    q1: DiscoveryExact
    q3: DiscoveryExact
    lower_fence: DiscoveryExact
    upper_fence: DiscoveryExact
    side: Literal["lower", "upper"]
    student_denominator: int


class BoundaryMetrics(Model):
    strict_count: int
    inclusive_count: int
    added_count: int
    removed_count: Literal[0]
    student_denominator: int
    eligible_denominator: int
    unevaluable_count: int
    share_strict_total: DiscoveryExact | None
    share_inclusive_total: DiscoveryExact | None
    share_strict_eligible: DiscoveryExact | None
    share_inclusive_eligible: DiscoveryExact | None


class RelationPoint(Model):
    x: DiscoveryExact
    y: DiscoveryExact
    count: int


class DiscoveryFrequency(Model):
    score: DiscoveryExact
    count: int
    student_denominator: int


class DiscoveryDimensionStats(Model):
    valid_count: int
    missing_count: int
    invalid_count: int
    mean: DiscoveryExact | None
    median: DiscoveryExact | None
    distribution: tuple[DiscoveryFrequency, ...]


class GroupPoint(Model):
    dimension_code: DimensionCode
    condition_defined: bool
    target: DiscoveryDimensionStats
    remainder: DiscoveryDimensionStats
    difference: DiscoveryExact | None


class RarePoint(Model):
    difference: DiscoveryExact
    count: int


class BoundaryPoint(Model):
    operator: Literal["lt", "lte"]
    count: int


type DiscoveryMetrics = RelationMetrics | GroupMetrics | RareMetrics | BoundaryMetrics
type DiscoveryChartData = (
    tuple[RelationPoint, ...]
    | tuple[GroupPoint, ...]
    | tuple[RarePoint, ...]
    | tuple[BoundaryPoint, ...]
)


class DiscoveryRobustness(Model):
    method: str
    planned: int
    evaluated: int
    passed: int
    value: DiscoveryExact | None


class DiscoveryRanking(Model):
    score: DiscoveryExact
    effect: DiscoveryExact
    support_count: int
    support: DiscoveryExact
    completeness: DiscoveryExact
    robustness: DiscoveryRobustness
    is_statistical_confidence: Literal[False]


class DiscoveryParameters(Model):
    minimum_samples: int
    support_target: int
    search_budget: int
    maximum_findings: int
    maximum_students: int
    correlation_threshold: DiscoveryExact
    mean_difference: DiscoveryExact
    rare_share: DiscoveryExact
    minimum_contrast: DiscoveryExact


class DiscoverySourceQuery(Model):
    discovery_id: str
    metric_key: str


class DiscoveryChart(Model):
    scope: DiscoveryScope
    discovery_id: str
    input_key: str
    rule_version: str
    type: Literal[
        "dimension_relation", "group_difference", "rare_combination", "boundary_sensitivity"
    ]
    conditions: tuple[DiscoveryCondition, ...]
    metrics: DiscoveryMetrics
    chart: DiscoveryChartData
    public_display_allowed: Literal[False] = False

    @model_validator(mode="after")
    def consistent_kind(self) -> "DiscoveryChart":
        check_kind(self.type, self.metrics, self.chart)
        return self


class DiscoveryDetail(DiscoverySummary):
    scope: DiscoveryScope
    rule_id: str
    parameters: DiscoveryParameters
    metrics: DiscoveryMetrics
    chart: DiscoveryChartData
    conditions: tuple[DiscoveryCondition, ...]
    limitations: tuple[str, ...]
    ranking: DiscoveryRanking
    controlled_queries: tuple[DiscoverySourceQuery, ...]

    @model_validator(mode="after")
    def consistent_kind(self) -> "DiscoveryDetail":
        check_kind(self.type, self.metrics, self.chart)
        return self


class DiscoveryRecords(DiscoveryParticipants):
    discovery_id: str
    rule_version: str
    conditions: tuple[DiscoveryCondition, ...]
    source_dimensions: tuple[DimensionCode, ...]


def check_kind(kind: str, metrics: DiscoveryMetrics, chart: DiscoveryChartData) -> None:
    metric_type, point_type = {
        "dimension_relation": (RelationMetrics, RelationPoint),
        "group_difference": (GroupMetrics, GroupPoint),
        "rare_combination": (RareMetrics, RarePoint),
        "boundary_sensitivity": (BoundaryMetrics, BoundaryPoint),
    }[kind]
    if not isinstance(metrics, metric_type) or any(not isinstance(p, point_type) for p in chart):
        raise ValueError("DISCOVERY_PROJECTION_KIND_MISMATCH")
