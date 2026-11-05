"""Proposed integration DTOs, not a replacement of the existing saved-result contract."""

from typing import Literal

from app.edu.analysis_models import ExactRational, FilterCondition, GroupQuery
from app.edu.models import Identifier, Model
from app.edu.review_models import AnalysisCreate
from pydantic import Field


class InvestigationRequest(Model):
    snapshot_id: Identifier
    question: str = Field(min_length=1, max_length=300)
    mode: Literal["http_mock"] = "http_mock"
    strategy: Literal["adaptive", "fixed"] = "adaptive"


class SourceRequest(Model):
    snapshot_id: Identifier
    fact_id: Identifier
    metric_key: str = Field(min_length=1, max_length=80)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class Metric(Model):
    key: str
    exact: ExactRational | None = None
    number: int | float | None = None
    display: str


class SourceReference(Model):
    metric_key: str
    dimension_code: str
    participant_count: int
    witness_kind: Literal["score", "row"]
    records_path: str | None
    records_query: SourceRequest | None


class FactResult(Model):
    fact_id: str
    batch_id: str
    snapshot_id: str
    method: str
    rule_version: str
    qualified: bool
    source_verified: bool
    conditions: tuple[FilterCondition, ...] = ()
    metrics: tuple[Metric, ...]
    source_refs: tuple[SourceReference, ...]
    limitation_codes: tuple[str, ...]
    exploration_query: GroupQuery | None = None
    save_group_analysis_request: AnalysisCreate | None = None


class ToolStep(Model):
    tool: str | None
    round: int
    phase: str
    status: str
    fact_ids: tuple[str, ...]


class InvocationBudget(Model):
    model_requests: int
    max_model_requests: Literal[6] = 6
    tool_attempts: int
    max_tool_attempts: Literal[5] = 5
    verification_replays: int
    actual_remote_requests: Literal[0] = 0
    actual_tokens: None = None
    invoice_cost_cny: None = None


class InvestigationResult(Model):
    run_id: str
    batch_id: str
    snapshot_id: str
    source_manifest_sha256: str
    scope_kind: Literal["whole_authorized_batch"] = "whole_authorized_batch"
    batch_record_count: int
    execution_origin: Literal["http_mock"] = "http_mock"
    strategy: Literal["adaptive", "fixed"]
    real_qwen: Literal["not_run"] = "not_run"
    production_enabled: Literal[False] = False
    status: str
    stop_reason: str
    controlled_goal: str
    goal_items: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    facts: tuple[FactResult, ...]
    selected_fact_ids: tuple[str, ...]
    retained_fact_ids: tuple[str, ...]
    trace: tuple[ToolStep, ...]
    budget: InvocationBudget
    uncovered_direction_count: int
    investigation_exhaustive: Literal[False] = False
    report_integration: Literal["group_statistics_only_agent_facts_not_persisted"] = (
        "group_statistics_only_agent_facts_not_persisted"
    )


class EvidenceLink(Model):
    student_id: str
    evidence_id: str
    dimension_code: str
    evidence_path: str


class SourceResult(Model):
    batch_id: str
    snapshot_id: str
    run_id: str
    fact_id: str
    metric_key: str
    total_count: int
    offset: int
    items: tuple[EvidenceLink, ...]
