"""Internal immutable contracts; neither authentication proofs nor public DTOs."""

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

from ..models import (
    AnalysisInputError,
    Condition,
    Correlations,
    Dimension,
    DimensionStats,
    PairCorrelation,
    RecordRef,
    Scope,
    Selection,
    number,
)

VERSION = "edu-discovery-0.1.1-draft"
DiscoveryType = Literal[
    "dimension_relation", "group_difference", "rare_combination", "boundary_sensitivity"
]
Status = Literal["displayable", "interpret_cautiously", "insufficient_evidence"]


@dataclass(frozen=True)
class Policy:
    minimum_samples: int = 5
    support_target: int = 20
    search_budget: int = 20
    maximum_findings: int = 12
    maximum_students: int = 5000
    correlation_threshold: Fraction = Fraction(3, 5)
    mean_difference: Fraction = Fraction(1, 2)
    rare_share: Fraction = Fraction(1, 10)
    minimum_contrast: Fraction = Fraction(1, 2)

    def __post_init__(self) -> None:
        for key, low, high in (
            ("minimum_samples", 2, 10000),
            ("support_target", 2, 10000),
            ("search_budget", 0, 50),
            ("maximum_findings", 1, 50),
            ("maximum_students", 1, 10000),
        ):
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise AnalysisInputError("invalid_discovery_budget")
        for key, high in (
            ("correlation_threshold", 1),
            ("mean_difference", 4),
            ("rare_share", 1),
            ("minimum_contrast", 8),
        ):
            value = number(getattr(self, key))
            if not 0 < value <= high:
                raise AnalysisInputError("invalid_discovery_threshold")
            object.__setattr__(self, key, value)


@dataclass(frozen=True)
class Context:
    batch_id: str
    snapshot_id: str
    scope: Scope = field(repr=False)


@dataclass(frozen=True)
class Robustness:
    method: str
    planned: int
    evaluated: int
    passed: int

    @property
    def value(self) -> Fraction | None:
        return Fraction(self.passed, self.planned) if self.evaluated and self.planned else None


@dataclass(frozen=True)
class Ranking:
    effect: Fraction
    support_count: int
    support_component: Fraction
    completeness: Fraction
    robustness: Robustness

    @property
    def score(self) -> Fraction:
        return (
            4 * self.effect
            + 2 * self.support_component
            + 2 * self.completeness
            + 2 * (self.robustness.value or 0)
        ) / 10


@dataclass(frozen=True)
class SourceSet:
    metric_key: str
    dimensions: tuple[Dimension, ...]
    records: tuple[RecordRef, ...] = field(repr=False)


@dataclass(frozen=True)
class DimensionDifference:
    dimension: Dimension
    target: DimensionStats = field(repr=False)
    remainder: DimensionStats = field(repr=False)
    difference: Fraction | None
    condition_defined: bool


@dataclass(frozen=True)
class GroupComparison:
    selection: Selection = field(repr=False)
    remainder: tuple[RecordRef, ...] = field(repr=False)
    dimensions: tuple[DimensionDifference, ...] = field(repr=False)
    outcome_dimension: Dimension | None = None


@dataclass(frozen=True)
class RareCombination:
    left: Dimension
    right: Dimension
    pair_count: int
    excluded_count: int
    q1: Fraction
    q3: Fraction
    median: Fraction
    lower_fence: Fraction
    upper_fence: Fraction
    side: Literal["lower", "upper"]
    pattern: Literal["individual_review", "small_group_review", "group_pattern"]
    distribution: tuple[tuple[Fraction, int], ...]
    matches: tuple[RecordRef, ...] = field(repr=False)
    pair_records: tuple[RecordRef, ...] = field(repr=False)


@dataclass(frozen=True)
class BoundaryRequest:
    conditions: tuple[Condition, ...]
    varying_dimension: Dimension


@dataclass(frozen=True)
class BoundaryComparison:
    strict: Selection = field(repr=False)
    inclusive: Selection = field(repr=False)
    added: tuple[RecordRef, ...] = field(repr=False)
    observed_boundary: bool
    status: Status


Payload = PairCorrelation | GroupComparison | RareCombination | BoundaryComparison


@dataclass(frozen=True)
class Discovery:
    discovery_id: str
    type: DiscoveryType
    context: Context
    title: str
    summary: str
    payload: Payload = field(repr=False)
    ranking: Ranking
    status: Status
    sources: tuple[SourceSet, ...] = field(repr=False)
    limitations: tuple[str, ...]
    policy: Policy
    rule_version: str = VERSION


@dataclass(frozen=True)
class SearchAttempt:
    conditions: tuple[Condition, ...]
    outcome: str


@dataclass(frozen=True)
class Quality:
    dimension: Dimension
    valid: int
    missing: int
    invalid: int


@dataclass(frozen=True)
class DiscoveryResult:
    context: Context
    findings: tuple[Discovery, ...]
    correlations: Correlations = field(repr=False)
    search_audit: tuple[SearchAttempt, ...]
    quality: tuple[Quality, ...]
    policy: Policy
    qualified_count: int
