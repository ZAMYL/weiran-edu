"""Internal offline contracts, not HTTP DTOs or authentication credentials."""

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.analysis.edu import Dataset, Dimension, EvidenceRef
from app.analysis.edu.models import Condition, RecordRef, Scope

type Identifier = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=160, strip_whitespace=False)
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvidenceGrant(StrictModel):
    evidence_id: Identifier
    record: RecordRef = Field(repr=False)
    reference: EvidenceRef = Field(repr=False)


class RubricGrant(StrictModel):
    evidence_id: Identifier
    batch_id: Identifier
    dimension: Dimension
    reference: EvidenceRef = Field(repr=False)
    version: Identifier


class SyntheticAccess(StrictModel):
    """Trusted caller supplies a synthetic-only authorization snapshot, never the model."""

    batch_id: Identifier
    student_ids: tuple[Identifier, ...] = Field(repr=False)
    evidence_grants: tuple[EvidenceGrant, ...] = Field(default=(), repr=False)
    rubric_grants: tuple[RubricGrant, ...] = Field(default=(), repr=False)
    synthetic_only: Literal[True] = True


@dataclass(frozen=True)
class EvidenceDocument:
    grant: EvidenceGrant = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True)
class RubricDocument:
    grant: RubricGrant = field(repr=False)
    text: str = field(repr=False)


class EvidenceProvider(Protocol):
    def get(self, access: SyntheticAccess, evidence_id: str) -> EvidenceDocument | None: ...


class RubricProvider(Protocol):
    def get(self, access: SyntheticAccess, evidence_id: str) -> RubricDocument | None: ...


class LLMClient(Protocol):
    """Implementations must be cooperative async, offline stubs in this release."""

    offline_only: Literal[True]

    async def plan(self, prompt: str) -> str: ...

    async def compose(self, prompt: str) -> str: ...


class Limits(StrictModel):
    max_tool_calls: int = Field(default=8, ge=1, le=16)
    max_scope_records: int = Field(default=1000, ge=1, le=10000)
    max_tool_bytes: int = Field(default=64000, ge=256, le=1000000)
    max_total_tool_bytes: int = Field(default=128000, ge=256, le=2000000)
    max_model_bytes: int = Field(default=32000, ge=256, le=128000)
    max_document_chars: int = Field(default=4000, ge=1, le=16000)
    timeout_seconds: float = Field(default=5.0, gt=0, le=60, allow_inf_nan=False)


@dataclass(frozen=True)
class Fact:
    fact_id: str
    batch_id: str
    rule_id: str
    rule_version: str
    metric: str
    dimension: Dimension | None
    value: Fraction | None
    denominator: int
    scope: Scope = field(repr=False)
    source_refs: tuple[RecordRef, ...] = field(repr=False)
    missing_count: int | None = 0
    invalid_count: int | None = 0
    unevaluable_count: int = 0
    conditions: tuple[Condition, ...] = ()
    limitations: tuple[str, ...] = ("source_content_not_verified",)

    def model_view(self) -> dict[str, object]:
        # Raw student/source identifiers, evidence locators and individual scores stay local.
        return {
            "fact_id": self.fact_id,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "metric": self.metric,
            "dimension": self.dimension.name if self.dimension else None,
            "exact_value": (
                {"numerator": self.value.numerator, "denominator": self.value.denominator}
                if self.value is not None
                else None
            ),
            "denominator": self.denominator,
            "scope_count": self.scope.student_count,
            "missing_count": self.missing_count,
            "invalid_count": self.invalid_count,
            "unevaluable_count": self.unevaluable_count,
            "conditions": [
                {
                    "dimension": item.dimension.name,
                    "operator": item.operator.value,
                    "threshold": str(item.threshold),
                }
                for item in self.conditions
            ],
            "limitations": self.limitations,
        }


class ToolCall(StrictModel):
    name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
    arguments: dict[str, object]


class Plan(StrictModel):
    calls: Annotated[tuple[ToolCall, ...], Field(min_length=1, max_length=16)]


class ObservationChoice(StrictModel):
    fact_id: Identifier


class BasedOnFacts(StrictModel):
    fact_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=80)]
    evidence_ids: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()


class HypothesisChoice(BasedOnFacts):
    kind: Literal["task_alignment", "practice_opportunity"]
    status: Literal["hypothesis"]


class ActionChoice(BasedOnFacts):
    kind: Literal["guided_practice", "review_rubric"]


class VerificationChoice(BasedOnFacts):
    kind: Literal["collect_work_samples", "repeat_comparable_assessment"]


type LimitationCode = Literal[
    "correlation_is_not_causation",
    "hypotheses_unconfirmed",
    "no_teaching_effect_verified",
    "templates_need_teacher_review",
    "offline_synthetic_not_auth",
    "rubric_not_retrieved",
    "evidence_not_retrieved",
    "missing_or_invalid_scores",
    "insufficient_data",
    "source_content_not_verified",
    "no_personal_text_interpretation",
]


class Proposal(StrictModel):
    # No model-authored factual prose or numeric claims are accepted.
    observed_facts: Annotated[tuple[ObservationChoice, ...], Field(min_length=1, max_length=80)]
    hypotheses: Annotated[tuple[HypothesisChoice, ...], Field(max_length=8)]
    teaching_actions: Annotated[tuple[ActionChoice, ...], Field(max_length=8)]
    verification_steps: Annotated[tuple[VerificationChoice, ...], Field(max_length=8)]
    limitations: Annotated[tuple[LimitationCode, ...], Field(max_length=11)]
    cited_fact_ids: Annotated[tuple[Identifier, ...], Field(max_length=80)]
    cited_evidence_ids: Annotated[tuple[Identifier, ...], Field(max_length=20)]


@dataclass(frozen=True)
class Observation:
    fact_id: str
    text: str


@dataclass(frozen=True)
class Explanation:
    kind: str
    text: str
    cited_fact_ids: tuple[str, ...]
    cited_evidence_ids: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class InsightReport:
    observed_facts: tuple[Observation, ...]
    hypotheses: tuple[Explanation, ...]
    teaching_actions: tuple[Explanation, ...]
    verification_steps: tuple[Explanation, ...]
    limitations: tuple[str, ...]
    cited_fact_ids: tuple[str, ...]
    cited_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class TraceStep:
    sequence: int
    tool: str
    status: str
    error_code: str | None
    fact_ids: tuple[str, ...] = ()
    evidence_count: int = 0
    output_bytes: int = 0


@dataclass(frozen=True)
class ToolResult:
    status: str
    error_code: str | None = None
    facts: tuple[Fact, ...] = field(default=(), repr=False)
    evidence_ids: tuple[str, ...] = ()
    documents: tuple[EvidenceDocument | RubricDocument, ...] = field(default=(), repr=False)

    def model_view(self) -> dict[str, object]:
        return {
            "status": self.status,
            "error_code": self.error_code,
            "facts": [fact.model_view() for fact in self.facts],
            "evidence_ids": self.evidence_ids,
            "evidence_metadata": [
                {
                    "evidence_id": item.grant.evidence_id,
                    "kind": item.grant.reference.kind.value,
                    "dimension": (
                        item.grant.reference.dimension.name
                        if item.grant.reference.dimension
                        else None
                    ),
                    "rubric_version": (
                        item.grant.version if isinstance(item, RubricDocument) else None
                    ),
                }
                for item in self.documents
            ],
        }


@dataclass(frozen=True)
class Outcome:
    status: str
    report: InsightReport | None
    error_code: str | None
    trace: tuple[TraceStep, ...]
    facts: tuple[Fact, ...] = field(default=(), repr=False)


SAFE_ERRORS = frozenset(
    {
        "invalid_authorization_scope",
        "scope_limit_exceeded",
        "ambiguous_evidence_grant",
        "invalid_evidence_grant",
        "invalid_rubric_grant",
        "tool_call_limit",
        "tool_not_allowed",
        "tool_arguments_too_large",
        "batch_not_authorized",
        "scope_not_authorized",
        "tool_output_limit",
        "invalid_tool_arguments",
        "analysis_input_error",
        "tool_provider_failure",
        "evidence_content_missing",
        "evidence_content_limit",
        "evidence_not_authorized",
        "evidence_missing",
        "evidence_binding_mismatch",
        "rubric_missing",
        "rubric_binding_mismatch",
        "rubric_incomplete",
        "invalid_model_output",
        "model_output_limit",
        "duplicate_json_key",
        "invalid_json_constant",
        "duplicate_citation",
        "unknown_fact_reference",
        "unknown_evidence_reference",
        "unobserved_fact_basis",
        "citation_union_mismatch",
        "offline_client_required",
        "synthetic_input_required",
        "invalid_synthetic_question",
        "prompt_size_limit",
        "unsupported_fact_basis",
        "evidence_fact_scope_mismatch",
    }
)


class InsightError(ValueError):
    """Unknown exception text cannot accidentally become a public error message."""

    def __init__(self, code: str) -> None:
        super().__init__(code if code in SAFE_ERRORS else "workflow_failure")


@dataclass(frozen=True)
class SyntheticInput:
    dataset: Dataset = field(repr=False)
    access: SyntheticAccess = field(repr=False)
