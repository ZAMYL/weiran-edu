"""One request-scoped whitelist. No SQL, file access, network, or raw-text logging."""

import json
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError

from app.analysis.edu import (
    AnalysisInputError,
    Condition,
    Dataset,
    Dimension,
    EvidenceKind,
    Operator,
    select_students,
    summarize,
)
from app.analysis.edu.engine import cohort
from app.analysis.edu.models import RecordRef, Scope

from .models import (
    EvidenceDocument,
    EvidenceProvider,
    Fact,
    Identifier,
    InsightError,
    Limits,
    RubricDocument,
    RubricProvider,
    StrictModel,
    SyntheticAccess,
    ToolCall,
    ToolResult,
    TraceStep,
)

type DimensionName = Literal["ALGORITHMS", "IMPLEMENTATION", "MODELING", "EFFICIENCY", "STRATEGY"]


class ScopeArguments(StrictModel):
    batch_id: Identifier
    student_ids: Annotated[tuple[Identifier, ...], Field(max_length=1000)] | None = None


class FilterCondition(StrictModel):
    dimension: DimensionName
    operator: Literal["<", "<=", "==", ">=", ">"]
    threshold: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[+-]?[0-9]{1,3}(?:\.[0-9]{1,4})?$")
    ]


class FilterArguments(ScopeArguments):
    conditions: Annotated[tuple[FilterCondition, ...], Field(min_length=1, max_length=5)]


class EvidenceArguments(StrictModel):
    batch_id: Identifier
    evidence_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=20)]


class RubricArguments(StrictModel):
    batch_id: Identifier


TOOL_SCHEMAS = {
    "get_dimension_statistics": ScopeArguments,
    "filter_student_group": FilterArguments,
    "get_evidence_records": EvidenceArguments,
    "get_rubric_criteria": RubricArguments,
}
RULE_VERSION = "edu-analysis-v0.1-draft/ai-projection-v0.1"


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


class ToolExecutor:
    """Create afresh for every workflow; never share mutable ledgers across requests."""

    def __init__(
        self,
        dataset: Dataset,
        access: SyntheticAccess,
        evidence_provider: EvidenceProvider,
        rubric_provider: RubricProvider,
        *,
        limits: Limits | None = None,
    ) -> None:
        self.dataset = dataset
        self.access = access
        self.evidence_provider = evidence_provider
        self.rubric_provider = rubric_provider
        self.limits = limits or Limits()
        self.facts: dict[str, Fact] = {}
        self.evidence_ids: set[str] = set()
        self.rubric_ids: set[str] = set()
        self.documents: dict[str, EvidenceDocument | RubricDocument] = {}
        self.trace: list[TraceStep] = []
        self._calls = 0
        self._bytes = 0
        self._validate_access()

    def _validate_access(self) -> None:
        try:
            scope, _ = cohort(self.dataset, self.access.batch_id, self.access.student_ids)
        except AnalysisInputError:
            raise InsightError("invalid_authorization_scope") from None
        if len(set(self.access.student_ids)) != len(self.access.student_ids):
            raise InsightError("invalid_authorization_scope")
        if scope.student_count > self.limits.max_scope_records:
            raise InsightError("scope_limit_exceeded")
        evidence_ids = [item.evidence_id for item in self.access.evidence_grants]
        rubric_ids = [item.evidence_id for item in self.access.rubric_grants]
        if len(set(evidence_ids + rubric_ids)) != len(evidence_ids + rubric_ids):
            raise InsightError("ambiguous_evidence_grant")
        for grant in self.access.evidence_grants:
            if (
                grant.record not in scope.records
                or grant.reference not in grant.record.evidence_refs
                or grant.reference.kind not in (EvidenceKind.SCORE, EvidenceKind.COMMENT)
            ):
                raise InsightError("invalid_evidence_grant")
        for grant in self.access.rubric_grants:
            if (
                grant.batch_id != self.access.batch_id
                or grant.reference.kind is not EvidenceKind.BASIS
                or grant.reference.dimension is not grant.dimension
            ):
                raise InsightError("invalid_rubric_grant")

    def execute(self, call: ToolCall) -> ToolResult:
        name = call.name if call.name in TOOL_SCHEMAS else "unknown"
        self._calls += 1
        if self._calls > self.limits.max_tool_calls:
            result = ToolResult("error", "tool_call_limit")
        else:
            try:
                if name == "unknown":
                    raise InsightError("tool_not_allowed")
                payload = encoded(call.arguments)
                if len(payload) > self.limits.max_model_bytes:
                    raise InsightError("tool_arguments_too_large")
                args = TOOL_SCHEMAS[name].model_validate_json(payload)
                if args.batch_id != self.access.batch_id:
                    raise InsightError("batch_not_authorized")
                if isinstance(args, ScopeArguments):
                    students = (
                        self.access.student_ids if args.student_ids is None else args.student_ids
                    )
                    if not set(students) <= set(self.access.student_ids):
                        raise InsightError("scope_not_authorized")
                    result = self._analyze(args, students)
                elif isinstance(args, EvidenceArguments):
                    result = self._evidence(args)
                else:
                    result = self._rubric()
                size = len(encoded(result.model_view())) + sum(
                    len(item.text.encode("utf-8")) for item in result.documents
                )
                if size > self.limits.max_tool_bytes or self._bytes + size > (
                    self.limits.max_total_tool_bytes
                ):
                    raise InsightError("tool_output_limit")
                # Atomic publication: rejected output must not leave usable IDs behind.
                self.facts.update((item.fact_id, item) for item in result.facts)
                self.evidence_ids.update(result.evidence_ids)
                self.documents.update((item.grant.evidence_id, item) for item in result.documents)
                if name == "get_rubric_criteria":
                    self.rubric_ids.update(result.evidence_ids)
                self._bytes += size
            except ValidationError:
                result = ToolResult("error", "invalid_tool_arguments")
            except InsightError as exc:
                result = ToolResult("error", str(exc))
            except AnalysisInputError:
                result = ToolResult("error", "analysis_input_error")
            except Exception:
                result = ToolResult("error", "tool_provider_failure")
        # Bound even denial traces. No question, arguments, source IDs, text or exceptions.
        if len(self.trace) <= self.limits.max_tool_calls:
            self.trace.append(
                TraceStep(
                    min(self._calls, self.limits.max_tool_calls + 1),
                    name,
                    result.status,
                    result.error_code,
                    tuple(item.fact_id for item in result.facts),
                    len(result.evidence_ids),
                    size if result.status == "ok" else 0,
                )
            )
        return result

    def _analyze(self, args: ScopeArguments, students: tuple[str, ...]) -> ToolResult:
        facts: list[Fact] = []

        def add(
            metric: str,
            dimension: Dimension | None,
            value: Fraction | int | None,
            denominator: int,
            scope: Scope,
            refs: tuple[RecordRef, ...],
            missing: int | None = 0,
            invalid: int | None = 0,
            unevaluable: int = 0,
            query: tuple[Condition, ...] = (),
        ) -> None:
            facts.append(
                Fact(
                    fact_id=f"fact-{len(self.facts) + len(facts) + 1:04d}",
                    batch_id=self.access.batch_id,
                    rule_id=f"edu.{metric}",
                    rule_version=RULE_VERSION,
                    metric=metric,
                    dimension=dimension,
                    value=Fraction(value) if value is not None else None,
                    denominator=denominator,
                    scope=scope,
                    source_refs=refs,
                    missing_count=missing,
                    invalid_count=invalid,
                    unevaluable_count=unevaluable,
                    conditions=query,
                    limitations=(
                        "source_content_not_verified",
                        *(
                            ("missing_or_invalid_scores",)
                            if missing or invalid or unevaluable
                            else ()
                        ),
                    ),
                )
            )

        if isinstance(args, FilterArguments):
            conditions = tuple(
                Condition(
                    Dimension[item.dimension], Operator(item.operator), Fraction(item.threshold)
                )
                for item in args.conditions
            )
            selected = select_students(
                self.dataset,
                batch_id=args.batch_id,
                student_ids=students,
                conditions=conditions,
            )
            share = selected.matched_share
            eligible = selected.matched_among_eligible
            for metric, value, denominator in (
                ("matched_count", share.count, share.denominator),
                ("matched_ratio", share.proportion, share.denominator),
                ("eligible_count", selected.eligible_count, share.denominator),
                ("matched_ratio_eligible", eligible.proportion, eligible.denominator),
                ("unevaluable_count", len(selected.unevaluable_records), share.denominator),
            ):
                add(
                    metric,
                    None,
                    value,
                    denominator,
                    selected.scope,
                    selected.matches,
                    missing=None,
                    invalid=None,
                    unevaluable=len(selected.unevaluable_records),
                    query=conditions,
                )
        else:
            summary = summarize(self.dataset, batch_id=args.batch_id, student_ids=students)
            for stats in summary.dimensions:
                for metric, value, denominator in (
                    ("valid_count", stats.valid_count, summary.scope.student_count),
                    ("missing_count", stats.missing_count, summary.scope.student_count),
                    ("invalid_count", stats.invalid_count, summary.scope.student_count),
                    ("mean", stats.mean, stats.valid_count),
                    ("median", stats.median, stats.valid_count),
                    ("minimum", stats.minimum, stats.valid_count),
                    ("maximum", stats.maximum, stats.valid_count),
                ):
                    add(
                        metric,
                        stats.dimension,
                        value,
                        denominator,
                        summary.scope,
                        stats.valid_records
                        if metric in {"mean", "median", "minimum", "maximum"}
                        else summary.scope.records,
                        stats.missing_count,
                        stats.invalid_count,
                    )
                for entry in stats.distribution:
                    add(
                        f"frequency_at_{entry.score}",
                        stats.dimension,
                        entry.share.count,
                        entry.share.denominator,
                        summary.scope,
                        stats.valid_records,
                        stats.missing_count,
                        stats.invalid_count,
                    )
        return ToolResult("ok", facts=tuple(facts))

    def _check_document(self, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise InsightError("evidence_content_missing")
        if (
            len(text) > self.limits.max_document_chars
            or len(text.encode("utf-8")) > self.limits.max_tool_bytes
        ):
            raise InsightError("evidence_content_limit")

    def _evidence(self, args: EvidenceArguments) -> ToolResult:
        grants = {item.evidence_id: item for item in self.access.evidence_grants}
        identifiers = tuple(sorted(set(args.evidence_ids)))
        if not set(identifiers) <= grants.keys():
            raise InsightError("evidence_not_authorized")
        documents = []
        for identifier in identifiers:
            document = self.evidence_provider.get(self.access, identifier)
            if document is None:
                raise InsightError("evidence_missing")
            if document.grant != grants[identifier]:
                raise InsightError("evidence_binding_mismatch")
            self._check_document(document.text)
            documents.append(document)
        return ToolResult("ok", evidence_ids=identifiers, documents=tuple(documents))

    def _rubric(self) -> ToolResult:
        grants = sorted(self.access.rubric_grants, key=lambda item: item.evidence_id)
        if not grants:
            raise InsightError("rubric_missing")
        if {item.dimension for item in grants} != set(Dimension) or len(grants) != len(Dimension):
            raise InsightError("rubric_incomplete")
        documents = []
        for grant in grants:
            document = self.rubric_provider.get(self.access, grant.evidence_id)
            if document is None:
                raise InsightError("rubric_missing")
            if document.grant != grant:
                raise InsightError("rubric_binding_mismatch")
            self._check_document(document.text)
            documents.append(document)
        return ToolResult(
            "ok",
            evidence_ids=tuple(item.evidence_id for item in grants),
            documents=tuple(documents),
        )
