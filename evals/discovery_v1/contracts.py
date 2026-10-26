"""Evaluation-only contracts, independent of app models and HTTP DTOs."""

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from typing import Protocol

VERSION = "edu-discovery-oracles-v1"
RULE_VERSION = "edu-discovery-0.1.1-draft"
DIMS = ("ALGORITHMS", "IMPLEMENTATION", "MODELING", "EFFICIENCY", "STRATEGY")
Number = int | Fraction | Decimal
Raw = int | Decimal | str | None


@dataclass(frozen=True)
class Predicate:
    dimension: str
    operator: str
    threshold: str


@dataclass(frozen=True)
class Query:
    kind: str
    left: str = ""
    right: str = ""
    conditions: tuple[Predicate, ...] = ()
    outcome: str = ""
    side: str = ""


@dataclass(frozen=True)
class Case:
    case_id: str
    seed: int
    recipe: str
    objective: str
    rows: tuple[tuple[Raw, ...], ...] = field(repr=False)
    targets: tuple[Query, ...]
    forbidden: tuple[str, ...]
    allowed_stops: tuple[str, ...] = ("completed", "no_valid_findings", "insufficient_data")
    distractors: tuple[tuple[Raw, ...], ...] = field(default=(), repr=False)

    @property
    def batch_id(self) -> str:
        return (
            "synthetic-batch-" + hashlib.sha256((VERSION + self.case_id).encode()).hexdigest()[:20]
        )

    @property
    def snapshot_id(self) -> str:
        return (
            "synthetic-snapshot-"
            + hashlib.sha256((self.case_id + VERSION).encode()).hexdigest()[:20]
        )

    @property
    def source_id(self) -> str:
        return "synthetic-source-" + hashlib.sha256(self.case_id.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class CandidateInput:
    """Local synthetic input only. No target, oracle labels or expected answers."""

    case_id: str
    batch_id: str
    snapshot_id: str
    source_id: str
    rows: tuple[tuple[Raw, ...], ...] = field(repr=False)
    distractors: tuple[tuple[Raw, ...], ...] = field(repr=False)
    boundary_requests: tuple[Query, ...] = ()
    question: str = "探索本批评分中可复核的统计现象；可以返回无有效发现。"


def candidate_input(case: Case) -> CandidateInput:
    boundaries = tuple(q for q in case.targets if q.kind == "boundary")
    return CandidateInput(
        case.source_id,
        case.batch_id,
        case.snapshot_id,
        case.source_id,
        case.rows,
        case.distractors,
        boundaries,
        "比较明确给定的边界条件。"
        if boundaries
        else "探索本批评分中可复核的统计现象；可以返回无有效发现。",
    )


def identity(case: Case | CandidateInput, index: int) -> tuple[str, str, str, str]:
    return (
        case.batch_id,
        f"synthetic-member-{index:04}",
        f"synthetic-record-{index:04}",
        case.source_id,
    )


def evidence(case: Case | CandidateInput, index: int, dimension: str) -> tuple[str, str, str, str]:
    return case.source_id, f"synthetic-score-{index:04}-{dimension}", "score", dimension


@dataclass(frozen=True)
class Binding:
    identity: tuple[str, str, str, str]
    evidence: tuple[tuple[str, str, str, str], ...]


@dataclass(frozen=True)
class Claim:
    fact_id: str
    run_id: str
    batch_id: str
    snapshot_id: str
    rule_version: str
    query: Query
    values: dict[str, object]
    bindings: dict[str, tuple[Binding, ...]] = field(repr=False)


@dataclass(frozen=True)
class Reference:
    values: dict[str, object]
    bindings: dict[str, tuple[Binding, ...]] = field(repr=False)
    qualified: bool
    reasons: tuple[str, ...]
    signature: str


@dataclass(frozen=True)
class CandidateRun:
    run_id: str
    status: str
    reason: str | None = None
    ledger: tuple[Claim, ...] = field(default=(), repr=False)
    selected_ids: tuple[str, ...] = ()
    compute_seconds: float | None = None
    engine_invocations: int = 0
    operation_counts: dict[str, int] = field(default_factory=dict)
    engine_qualified_count: int | None = None
    engine_returned_count: int | None = None
    search_attempts: int | None = None
    tool_trace: tuple[dict[str, object], ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: str | None = None
    presented_ids: tuple[str, ...] | None = None
    measurement_origin: str = "offline_deterministic"
    model_requests: int = 0


class Candidate(Protocol):
    """Later Agent adapters return actual controlled ledgers; no model code lives here."""

    def run(self, case: CandidateInput, profile: str) -> CandidateRun: ...
