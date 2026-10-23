"""Internal, reference-only outputs. Raw comment text never appears in result reprs."""

from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

from ..models import Dimension, EvidenceRef, RecordRef, Scope


class Context(Enum):
    AFFIRMATIVE = "affirmative"
    NEGATIVE = "negative"
    MIXED = "mixed"
    MENTION_ONLY = "mention_only"
    UNCERTAIN = "uncertain"


class ClassificationState(Enum):
    CLASSIFIED = "classified"
    UNCLASSIFIED = "unclassified"
    EMPTY = "empty"


class FindingKind(Enum):
    DUPLICATE = "duplicate_comment"
    SCORE_CONFLICT = "explicit_score_conflict_candidate"
    MANUAL = "manual_judgment"


class ReviewState(Enum):
    MANUAL = "manual_review"
    CANDIDATE = "conflict_candidate"
    NO_EXPLICIT_CONFLICT = "no_explicit_conflict"


@dataclass(frozen=True)
class CommentInput:
    assessment: RecordRef
    comment_id: str
    evidence: EvidenceRef
    text: str | None = field(repr=False)


@dataclass(frozen=True)
class CommentRef:
    assessment: RecordRef
    comment_id: str
    evidence: EvidenceRef


@dataclass(frozen=True)
class Hit:
    rule_id: str
    start: int
    end: int


@dataclass(frozen=True)
class TopicRule:
    rule_id: str
    dimension: Dimension
    title: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class ContextRule:
    rule_id: str
    dimension: Dimension
    context: Context
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class NumericRule:
    rule_id: str
    dimension: Dimension
    label: str


@dataclass(frozen=True)
class TopicMatch:
    dimension: Dimension
    title: str
    context: Context
    hits: tuple[Hit, ...]


@dataclass(frozen=True)
class NumericClaim:
    dimension: Dimension
    value: Fraction
    hit: Hit


@dataclass(frozen=True)
class Classification:
    comment: CommentRef
    rule_version: str
    rule_fingerprint: str
    rule_status: str
    state: ClassificationState
    topics: tuple[TopicMatch, ...]
    claims: tuple[NumericClaim, ...]
    guard_hits: tuple[Hit, ...]
    numeric_context_clear: bool


@dataclass(frozen=True)
class Finding:
    rule_id: str
    rule_version: str
    rule_fingerprint: str
    kind: FindingKind
    reason_code: str
    reason: str
    assessments: tuple[RecordRef, ...]
    comments: tuple[CommentRef, ...]
    hits: tuple[Hit, ...] = ()
    dimension: Dimension | None = None
    recorded_score: Fraction | None = None
    stated_score: Fraction | None = None
    score_evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class DuplicateGroup:
    rule_id: str
    rule_version: str
    rule_fingerprint: str
    comments: tuple[CommentRef, ...]

    @property
    def occurrences(self) -> int:
        return len(self.comments)

    @property
    def distinct_assessments(self) -> int:
        return len({comment.assessment for comment in self.comments})


@dataclass(frozen=True)
class RecordReview:
    assessment: RecordRef
    state: ReviewState
    classifications: tuple[Classification, ...]
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class ReviewReport:
    scope: Scope
    rule_version: str
    rule_fingerprint: str
    rule_status: str
    records: tuple[RecordReview, ...]
    duplicate_groups: tuple[DuplicateGroup, ...]
