"""Internal DS-04A contracts; only the suggestion models accept model input."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["programming", "evaluation", "problem_material", "reference", "other"]
Meaning = Literal[
    "student_key",
    "problem_key",
    "code",
    "language",
    "score",
    "verdict",
    "test_point",
    "test_result",
    "runtime",
    "memory",
    "started_at",
    "finished_at",
    "duration",
    "dimension",
    "dimension_score",
    "feedback",
    "rubric",
]
State = Literal["confirmed", "candidate", "needs_confirmation", "unsupported"]
Coverage = Literal["complete", "partial", "unknown"]
Gate = Literal[
    "replayable_source",
    "supported_code",
    "verified_five_dimensions",
    "teacher_analysis",
    "individual_evidence",
    "c01_admitted",
]
Cell = str | int | float | None


class ExtractedTableLike(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def header(self) -> tuple[str, ...]: ...

    @property
    def rows(self) -> tuple[tuple[Cell, ...], ...]: ...


class ExtractedDocumentLike(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def tables(self) -> tuple[ExtractedTableLike, ...]: ...

    @property
    def text_lines(self) -> tuple[str, ...]: ...

    @property
    def warnings(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, repr=False)
class TableAuthority:
    """Server-only attestations for this exact document/table revision.

    Do not construct from uploaded metadata or candidate output. A source ID, when
    supplied for a gate, identifies the upstream proof in the local evidence store.
    """

    table_index: int
    role: Role | None = None
    fields: tuple[tuple[int, Meaning], ...] = ()
    score_ranges: tuple[tuple[int, Decimal, Decimal], ...] = ()
    identity_namespace: str | None = None
    gates: tuple[tuple[Gate, str], ...] = ()
    blocked_columns: frozenset[int] = frozenset()
    blocked: bool = False


@dataclass(frozen=True, repr=False)
class MaterialInput:
    document: ExtractedDocumentLike
    source_id: str
    coverage: Coverage = "unknown"
    authority: tuple[TableAuthority, ...] = ()
    blocked: bool = False


@dataclass(frozen=True, repr=False)
class Locator:
    source_id: str
    table_index: int | None
    table_name: str | None
    column_index: int | None = None
    column_name: str | None = None


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    evidence: list[str] = Field(min_length=1, max_length=8)
    uncertain: bool = False


class RoleSuggestion(Suggestion):
    table: str
    role: Role


class FieldSuggestion(Suggestion):
    column: str
    meaning: Meaning


class LinkSuggestion(Suggestion):
    left: str
    right: str
    cardinality: Literal["one_to_one", "many_to_one"]


class Suggestions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    roles: list[RoleSuggestion] = Field(max_length=80)
    fields: list[FieldSuggestion] = Field(max_length=360)
    links: list[LinkSuggestion] = Field(max_length=40)


@dataclass(frozen=True)
class Verdict:
    state: State
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Assessment:
    roles: dict[tuple[str, Role], Verdict]
    fields: dict[tuple[str, Meaning], Verdict]
    links: dict[tuple[str, str, str], Verdict]


@dataclass(frozen=True)
class CapabilityRule:
    name: str
    module: str
    role: Role
    fields: frozenset[Meaning]
    gates: frozenset[Gate]
    requires_link_to: Role | None = None


@dataclass(frozen=True)
class Capability:
    name: str
    module: str
    table: str | None
    available: bool
    evidence: tuple[str, ...]
    missing: tuple[str, ...]
    needs_confirmation: bool


@dataclass(frozen=True)
class MaterialPlan:
    assessment: Assessment
    capabilities: tuple[Capability, ...]
    locators: dict[str, Locator] = field(repr=False)
    overview: dict = field(repr=False)
    needs_confirmation: tuple[str, ...] = ()
