"""Internal Python objects only; not an HTTP DTO or database schema."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from math import isfinite

type RawScore = int | float | Decimal | Fraction | str | bool | None


class AnalysisInputError(ValueError):
    """A structural error prevents ambiguous or partially accepted datasets."""


class Dimension(Enum):
    ALGORITHMS = "算法与数据结构"
    IMPLEMENTATION = "代码实现能力"
    MODELING = "问题建模能力"
    EFFICIENCY = "效率意识"
    STRATEGY = "解题策略"


class ScoreState(Enum):
    VALID = "valid"
    MISSING = "missing"
    INVALID = "invalid"


class EvidenceKind(Enum):
    SCORE = "score"
    COMMENT = "comment"
    BASIS = "basis"


class Operator(Enum):
    LT = "<"
    LE = "<="
    EQ = "=="
    GE = ">="
    GT = ">"


class CorrelationState(Enum):
    COMPUTABLE = "computable"
    INSUFFICIENT_PAIRS = "insufficient_pairs"
    CONSTANT_INPUT = "constant_input"


def number(value: object) -> Fraction:
    """Interpret finite numeric inputs exactly; never coerce text or booleans."""
    if isinstance(value, bool):
        raise AnalysisInputError("numeric_value_required")
    if isinstance(value, float):
        if not isfinite(value):
            raise AnalysisInputError("finite_value_required")
        return Fraction(str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AnalysisInputError("finite_value_required")
        return Fraction(value)
    if isinstance(value, (int, Fraction)):
        return Fraction(value)
    raise AnalysisInputError("numeric_value_required")


def require_id(value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AnalysisInputError("nonempty_unpadded_identifier_required")


@dataclass(frozen=True)
class ScorePolicy:
    minimum: Fraction = Fraction(1)
    maximum: Fraction = Fraction(5)
    step: Fraction | None = None
    missing_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "minimum", number(self.minimum))
        object.__setattr__(self, "maximum", number(self.maximum))
        if self.minimum >= self.maximum:
            raise AnalysisInputError("invalid_score_range")
        if self.step is not None:
            object.__setattr__(self, "step", number(self.step))
            if self.step <= 0:
                raise AnalysisInputError("positive_step_required")
        if not isinstance(self.missing_tokens, tuple) or not all(
            isinstance(token, str) for token in self.missing_tokens
        ):
            raise AnalysisInputError("missing_tokens_must_be_a_tuple_of_strings")


@dataclass(frozen=True)
class EvidenceRef:
    source_id: str
    location: str
    kind: EvidenceKind
    dimension: Dimension | None = None


@dataclass(frozen=True)
class AssessmentRecord:
    batch_id: str
    student_id: str
    record_id: str
    source_id: str
    scores: Mapping[Dimension, RawScore]
    evidence_refs: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class RecordRef:
    batch_id: str
    student_id: str
    record_id: str
    source_id: str
    evidence_refs: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class Score:
    dimension: Dimension
    raw_value: RawScore
    value: Fraction | None
    state: ScoreState
    reason: str | None = None


@dataclass(frozen=True)
class ValidatedRecord:
    reference: RecordRef
    scores: tuple[Score, ...]

    def score(self, dimension: Dimension) -> Score:
        return self.scores[list(Dimension).index(dimension)]


@dataclass(frozen=True)
class ScoreIssue:
    reference: RecordRef
    score: Score


@dataclass(frozen=True)
class Dataset:
    """Use prepare_dataset to construct a validated, immutable snapshot."""

    policy: ScorePolicy
    records: tuple[ValidatedRecord, ...]
    issues: tuple[ScoreIssue, ...]


@dataclass(frozen=True)
class Scope:
    batch_id: str
    policy: ScorePolicy
    requested_student_ids: tuple[str, ...] | None
    records: tuple[RecordRef, ...]

    @property
    def student_ids(self) -> tuple[str, ...]:
        return tuple(record.student_id for record in self.records)

    @property
    def student_count(self) -> int:
        return len(self.records)


@dataclass(frozen=True)
class CountShare:
    count: int
    denominator: int

    @property
    def proportion(self) -> Fraction | None:
        return Fraction(self.count, self.denominator) if self.denominator else None


@dataclass(frozen=True)
class Frequency:
    score: Fraction
    share: CountShare


@dataclass(frozen=True)
class DimensionStats:
    dimension: Dimension
    valid_count: int
    missing_count: int
    invalid_count: int
    mean: Fraction | None
    median: Fraction | None
    minimum: Fraction | None
    maximum: Fraction | None
    distribution: tuple[Frequency, ...]
    valid_records: tuple[RecordRef, ...]


@dataclass(frozen=True)
class Summary:
    scope: Scope
    dimensions: tuple[DimensionStats, ...]

    def dimension(self, dimension: Dimension) -> DimensionStats:
        return self.dimensions[list(Dimension).index(dimension)]


@dataclass(frozen=True)
class Condition:
    dimension: Dimension
    operator: Operator
    threshold: Fraction

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, Dimension) or not isinstance(self.operator, Operator):
            raise AnalysisInputError("typed_dimension_and_operator_required")
        object.__setattr__(self, "threshold", number(self.threshold))


@dataclass(frozen=True)
class Selection:
    scope: Scope
    conditions: tuple[Condition, ...]
    matches: tuple[RecordRef, ...]
    unevaluable_records: tuple[RecordRef, ...]

    @property
    def student_ids(self) -> tuple[str, ...]:
        return tuple(record.student_id for record in self.matches)

    @property
    def matched_share(self) -> CountShare:
        return CountShare(len(self.matches), self.scope.student_count)

    @property
    def eligible_count(self) -> int:
        return self.scope.student_count - len(self.unevaluable_records)

    @property
    def matched_among_eligible(self) -> CountShare:
        return CountShare(len(self.matches), self.eligible_count)


@dataclass(frozen=True)
class ProfileDimension:
    score: Score
    cohort: DimensionStats
    difference_from_mean: Fraction | None
    evidence_refs: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class StudentProfile:
    scope: Scope
    reference: RecordRef
    dimensions: tuple[ProfileDimension, ...]


@dataclass(frozen=True)
class RankedPair:
    reference: RecordRef
    left_score: Fraction
    right_score: Fraction
    left_rank: float
    right_rank: float


@dataclass(frozen=True)
class PairCorrelation:
    left: Dimension
    right: Dimension
    state: CorrelationState
    coefficient: float | None
    observations: tuple[RankedPair, ...]
    excluded_records: tuple[RecordRef, ...]

    @property
    def paired_count(self) -> int:
        return len(self.observations)


@dataclass(frozen=True)
class Correlations:
    scope: Scope
    minimum_pairs: int
    pairs: tuple[PairCorrelation, ...]
