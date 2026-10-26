"""Private runtime contracts, not teacher authentication or formal API DTOs."""

from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.analysis.edu.discovery.models import Discovery, SourceSet

DimensionName = Literal["ALGORITHMS", "IMPLEMENTATION", "MODELING", "EFFICIENCY", "STRATEGY"]
ToolName = Literal[
    "get_dimension_statistics",
    "query_dimension_relation",
    "compare_student_groups",
    "inspect_rare_combination",
    "verify_threshold_sensitivity",
    "inspect_discovery_support",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class EmptyArgs(Strict):
    pass


class PairArgs(Strict):
    left: DimensionName
    right: DimensionName


class Predicate(Strict):
    dimension: DimensionName
    operator: Literal["<", "<=", "==", ">=", ">"]
    threshold: str = Field(pattern=r"^[1-5](?:\.[0-9]{1,2})?$", max_length=4)


class GroupArgs(Strict):
    conditions: list[Predicate] = Field(min_length=1, max_length=2)
    outcomes: list[DimensionName] = Field(min_length=1, max_length=3)


class BoundaryArgs(Strict):
    conditions: list[Predicate] = Field(min_length=1, max_length=2)
    varying_dimension: DimensionName


class SupportArgs(Strict):
    fact_id: str = Field(min_length=1, max_length=80)


FactId = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9:_-]+$")]


class FinishArgs(Strict):
    selected_fact_ids: list[FactId] = Field(max_length=3)
    reason: Literal["completed", "no_valid_findings", "insufficient_data"]


class Decision(Strict):
    name: ToolName | Literal["finish"]
    arguments: dict[str, object]
    basis_fact_ids: list[FactId] = Field(default_factory=list, max_length=8)


SCHEMAS = {
    "get_dimension_statistics": EmptyArgs,
    "query_dimension_relation": PairArgs,
    "compare_student_groups": GroupArgs,
    "inspect_rare_combination": PairArgs,
    "verify_threshold_sensitivity": BoundaryArgs,
    "inspect_discovery_support": SupportArgs,
    "finish": FinishArgs,
}


class Planner(Protocol):
    offline_only: bool

    async def decide(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class Limits:
    model_requests: int = 6
    tool_attempts: int = 5
    timeout_seconds: float = 5
    run_seconds: float = 90
    output_bytes: int = 8192
    total_output_bytes: int = 32768
    prompt_bytes: int = 64000
    minimum_cell: int = 5

    def __post_init__(self):
        for name, maximum in (
            ("model_requests", 6),
            ("tool_attempts", 5),
            ("output_bytes", 8192),
            ("total_output_bytes", 32768),
            ("prompt_bytes", 64000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid_agent_budget")
        if type(self.minimum_cell) is not int or self.minimum_cell < 5 or self.minimum_cell > 100:
            raise ValueError("invalid_disclosure_policy")
        if type(self.timeout_seconds) not in (int, float) or not 0 < self.timeout_seconds <= 60:
            raise ValueError("invalid_agent_timeout")
        if type(self.run_seconds) not in (int, float) or not 0 < self.run_seconds <= 90:
            raise ValueError("invalid_agent_timeout")


@dataclass(frozen=True)
class Fact:
    fact_id: str
    tool: str
    view: dict[str, object]
    discovery: Discovery | None = field(default=None, repr=False)
    metric_sources: tuple[SourceSet, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class ToolEntry:
    attempt: int
    tool: str
    status: str
    fact_ids: tuple[str, ...] = ()
    error: str | None = None
    # Only typed, validated whitelisted arguments; no rejected raw model input.
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    sequence: int
    round: int
    phase: str
    status: str
    tool: str | None = None
    fact_ids: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class AgentRun:
    run_id: str
    status: str
    reason: str
    cards: tuple[dict[str, object], ...]
    selected_ids: tuple[str, ...]
    facts: tuple[Fact, ...] = field(repr=False)
    tools: tuple[ToolEntry, ...]
    trace: tuple[Event, ...]
    model_requests: int
    tool_attempts: int
    compute_seconds: float
    measurement_origin: str = "offline_mechanism"
    proposed_ids: tuple[str, ...] = field(default=(), repr=False)
    tool_compute_seconds: float = 0.0


class AgentError(ValueError):
    """Fixed codes only; raw model responses and transport errors never enter traces."""
