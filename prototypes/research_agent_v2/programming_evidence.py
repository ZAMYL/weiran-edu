"""Private provider contract for C-01. No parser, HTTP DTO or feature extractor."""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from prototypes.adaptive_assessment.data import Witness

from app.ai.edu.discovery.models import AgentError

FindingStatus = Literal[
    "wrong_answer", "timeout", "memory_limit", "runtime_error", "format_error", "other_failure"
]
TestStatus = FindingStatus | Literal["accepted", "unknown"]
Verdict = TestStatus | Literal["not_attempted", "compile_error", "partial"]
FeatureCode = Literal["boundary_check", "loop", "indexing", "recursion", "input_parsing"]


@dataclass(frozen=True)
class TestEvidence:
    point: str
    status: TestStatus
    ref: str


@dataclass(frozen=True, repr=False)
class SubmissionEvidence:
    participant: str
    problem: str
    verdict: Verdict
    verdict_ref: str
    code_ref: str | None = None
    tests: tuple[TestEvidence, ...] = ()


@dataclass(frozen=True, repr=False)
class FeatureAssociation:
    """DS-03 result for one failure cohort; the executor never inspects code to derive it."""

    problem: str
    point: str | None
    status: str
    code: FeatureCode
    checked: tuple[str, ...]
    present: tuple[tuple[str, str], ...]  # (participant, DS-03 feature Witness reference)


@dataclass(frozen=True, repr=False)
class EvidenceScope:
    # Server-owned task binding, never constructed from model arguments.
    actor: str
    task: str
    batch: str
    snapshot: str


@dataclass(frozen=True, repr=False)
class EvidenceSnapshot:
    snapshot: str
    problems: tuple[str, ...]
    participants: tuple[str, ...]
    submissions: tuple[SubmissionEvidence, ...]
    witnesses: tuple[Witness, ...]
    features: tuple[FeatureAssociation, ...] = ()


class EvidenceProvider(Protocol):
    def capture(self, scope: EvidenceScope) -> EvidenceSnapshot:
        """Authorize and replay the bound snapshot; never read the latest batch instead."""

    def require_details(self, scope: EvidenceScope) -> None:
        """Recheck the caller's separate right to view individual evidence."""


@dataclass(frozen=True, repr=False)
class ProgrammingSource:
    scope: EvidenceScope
    provider: EvidenceProvider
    material: EvidenceSnapshot = field(init=False)

    def __post_init__(self):
        material = self.provider.capture(self.scope)
        if material.snapshot != self.scope.snapshot:
            raise AgentError("source_scope_mismatch")
        object.__setattr__(self, "material", material)

    @property
    def fingerprint(self) -> str:
        return self.scope.snapshot

    @property
    def recipe(self) -> str:
        return self.scope.batch

    def validate(self) -> None:
        if self.provider.capture(self.scope) != self.material:
            raise AgentError("source_validation_failed")
