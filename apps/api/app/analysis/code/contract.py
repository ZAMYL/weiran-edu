"""Input contract for the code-analysis kernel.

These are private, immutable Python objects, not an HTTP DTO or database schema.
The contract is deliberately small and self-contained so it can be adapted to the
B-01 acceptance data model once that lands (PR #105); no field here binds to an
unvalidated backend structure.

All identifiers follow the same rule as the five-dimension analytics: a non-empty,
non-padded string. Structural ambiguity (duplicate submissions, inconsistent
identity, duplicate test points) is rejected rather than silently deduplicated.
"""

from dataclasses import dataclass
from enum import Enum


class CodeAnalysisError(ValueError):
    """A fixed, input-independent code crosses this boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Language(Enum):
    """Programming languages the kernel can extract features from."""

    PYTHON = "python"
    CPP = "cpp"


class Verdict(Enum):
    """A judge result. ``UNKNOWN`` means no judge evidence was supplied.

    The set mirrors B-01's answer-sheet status words and C-01's status union so a
    submission can be classified into success / failure / other without a lossy
    string compare at the call site.
    """

    ACCEPTED = "accepted"
    WRONG_ANSWER = "wrong_answer"
    RUNTIME_ERROR = "runtime_error"
    TIME_LIMIT_EXCEEDED = "time_limit_exceeded"
    MEMORY_LIMIT_EXCEEDED = "memory_limit_exceeded"
    FORMAT_ERROR = "format_error"
    COMPILE_ERROR = "compile_error"
    PARTIAL = "partial"
    NOT_ATTEMPTED = "not_attempted"
    UNKNOWN = "unknown"

    @property
    def is_success(self) -> bool:
        return self is Verdict.ACCEPTED

    @property
    def is_failure(self) -> bool:
        return self in (
            Verdict.WRONG_ANSWER,
            Verdict.RUNTIME_ERROR,
            Verdict.TIME_LIMIT_EXCEEDED,
            Verdict.MEMORY_LIMIT_EXCEEDED,
            Verdict.FORMAT_ERROR,
        )


class TestPointOutcome(Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"

    __test__ = False  # not a pytest test class


@dataclass(frozen=True)
class TestPointResult:
    __test__ = False  # not a pytest test class

    testpoint_id: str
    outcome: TestPointOutcome


@dataclass(frozen=True)
class SourceRef:
    """Provenance of a submission or an evidence record."""

    source_id: str
    location: str


@dataclass(frozen=True)
class SubmissionRecord:
    """One actual submitted program with its judge and test-point evidence."""

    batch_id: str
    problem_id: str
    submission_id: str
    student_id: str
    language: Language
    source_code: str
    verdict: Verdict
    test_points: tuple[TestPointResult, ...]
    source: SourceRef


@dataclass(frozen=True)
class SubmissionRef:
    """A lightweight reference to a submission; never carries source text."""

    batch_id: str
    problem_id: str
    submission_id: str
    student_id: str
    source: SourceRef


def require_id(value: object, code: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CodeAnalysisError(code)


def reference_of(record: SubmissionRecord) -> SubmissionRef:
    return SubmissionRef(
        record.batch_id,
        record.problem_id,
        record.submission_id,
        record.student_id,
        record.source,
    )
