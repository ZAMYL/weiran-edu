"""Public deterministic analysis contract, independent of internal engine objects."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, WithJsonSchema, field_serializer, field_validator, model_validator

from app.core.exact_json import ExactDenominator, ExactNumerator
from app.importing.edu.preview import Page

from .models import Identifier, Model, Student

type DimensionCode = Literal[
    "algorithm_data_structure",
    "code_implementation",
    "problem_modeling",
    "efficiency_awareness",
    "solution_strategy",
]
type Comparison = Literal["lt", "lte", "eq", "gte", "gt"]
type Threshold = Annotated[
    Decimal, WithJsonSchema({"type": "number", "minimum": 1, "maximum": 5, "multipleOf": 0.01})
]


class FilterCondition(Model):
    dimension_code: DimensionCode
    operator: Comparison
    value: Threshold

    @field_validator("value", mode="before")
    @classmethod
    def numeric_threshold(cls, value: object) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("NUMERIC_THRESHOLD_REQUIRED")
        number = Decimal(str(value))
        if not number.is_finite() or not 1 <= number <= 5:
            raise ValueError("THRESHOLD_OUT_OF_RANGE")
        # Inspect digits without Decimal.normalize(), whose context could round input.
        digits = number.as_tuple().digits
        trailing = 0
        for digit in reversed(digits):
            if digit != 0:
                break
            trailing += 1
        if number.as_tuple().exponent + trailing < -2:
            raise ValueError("THRESHOLD_PRECISION")
        return number

    @field_serializer("value")
    def numeric_json(self, value: Decimal) -> float:
        return float(value)


class GroupQuery(Model):
    conditions: Annotated[tuple[FilterCondition, ...], Field(min_length=1, max_length=2)]
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    snapshot_id: Identifier | None = None
    include_statistics: bool = False

    @field_validator("conditions", mode="before")
    @classmethod
    def json_array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def distinct_dimensions(self) -> "GroupQuery":
        if len({c.dimension_code for c in self.conditions}) != len(self.conditions):
            raise ValueError("DISTINCT_DIMENSIONS_REQUIRED")
        return self


class ExactRational(Model):
    numerator: ExactNumerator
    denominator: ExactDenominator


class StatValue(Model):
    value: float | None
    exact: ExactRational | None
    display: str


class DisplayPolicy(Model):
    digits: Literal[2] = 2
    rounding: Literal["HALF_UP"] = "HALF_UP"


class AnalysisScope(Model):
    kind: Literal["batch", "matched_group"]
    record_count: int


class Frequency(Model):
    score: float
    count: int
    denominator: int
    ratio: StatValue


class DimensionStatistics(Model):
    dimension_code: DimensionCode
    valid_count: int
    missing_count: int
    invalid_count: int
    mean: StatValue
    median: StatValue
    minimum: StatValue
    maximum: StatValue
    distribution: tuple[Frequency, ...]


class MatrixCell(Model):
    x: float
    y: float
    count: int


class ScoreMatrix(Model):
    scope: Literal["batch"] = "batch"
    x_dimension: DimensionCode
    y_dimension: DimensionCode
    x_values: tuple[float, ...]
    y_values: tuple[float, ...]
    paired_count: int
    excluded_count: int
    cells: tuple[MatrixCell, ...]


class Statistics(Model):
    batch_id: str
    snapshot_id: str
    rule_version: str
    scope: AnalysisScope
    display_policy: DisplayPolicy = DisplayPolicy()
    dimensions: tuple[DimensionStatistics, ...]
    matrix: ScoreMatrix | None = None


class GroupResult(Model):
    batch_id: str
    snapshot_id: str
    conditions: tuple[FilterCondition, ...]
    rule_version: str
    matched_count: int
    total_count: int
    ratio: float
    eligible_count: int
    unevaluable_count: int
    matched_among_eligible_ratio: float | None
    items: tuple[Student, ...]
    page: Page
    statistics: Statistics | None
