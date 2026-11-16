"""Durable task DTOs. No client statistics, facts, consent flags or budgets."""

from typing import Literal

from prototypes.teacher_agent_bridge.models import InvestigationRequest, InvestigationResult
from pydantic import Field, JsonValue, field_validator

from app.edu.analysis_models import DimensionCode, ExactRational
from app.edu.models import Identifier, Model

PROFILE_RESULT_CONTRACT = "profile-investigation-v1"
STANDARD_RESULT_CONTRACT = "investigation-v1"
RESULT_CONTRACT_KEY = "_result_contract"
PROFILE_SCORE_EVIDENCE_MODE = "synthetic_task_profile_scores_v1"
LIVE_PROFILE_SCORE_EVIDENCE_MODE = "live_task_profile_scores_v1"


class TaskCreate(InvestigationRequest):
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ProfileProjection(Model):
    assessment_snapshot_id: str
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_rule_version: str
    explanation_rule_version: str
    minimum_cell: int = Field(ge=2)
    profiles: tuple[dict[str, JsonValue], ...] = Field(max_length=3)
    stop_reason: str
    investigation_status: str
    uninvestigated_candidates: int = Field(ge=0)
    search_audit: dict[str, JsonValue]
    timings_seconds: dict[str, float]
    verification_replays: int = Field(ge=0)
    dimension_evidence_http: Literal[
        "not_implemented",
        "synthetic_task_profile_scores_v1",
        "live_task_profile_scores_v1",
    ] = "not_implemented"


class ProfileInvestigationResult(InvestigationResult):
    profile_projection: ProfileProjection


class ProfileScoreRequest(Model):
    snapshot_id: Identifier
    profile_id: Identifier
    dimension_code: DimensionCode
    side: Literal["target", "remainder"]
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class ProfileScoreEvidence(Model):
    student_id: Identifier
    evidence_id: Identifier
    dimension_code: DimensionCode
    score: ExactRational
    evidence_path: str


class ProfileScoreResult(Model):
    task_id: Identifier
    batch_id: Identifier
    snapshot_id: Identifier
    profile_id: Identifier
    dimension_code: DimensionCode
    side: Literal["target", "remainder"]
    detail_status: Literal["available", "privacy_limited"]
    source_origin: Literal["deterministic_profile_replay"] = "deterministic_profile_replay"
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_profile_fact_ids: tuple[Identifier, ...]
    dimension_fact_ids: tuple[Identifier, ...]
    mean: ExactRational
    valid_count: int = Field(ge=0)
    total_count: int | None = Field(default=None, ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    has_more: bool
    items: tuple[ProfileScoreEvidence, ...]
    limitation_code: Literal["PROFILE_SCORE_DETAIL_PRIVACY_LIMITED"] | None = None


class TaskView(Model):
    task_id: str
    batch_id: str
    snapshot_id: str
    campaign_id: str
    state: str
    error_code: str | None
    stop_reason: str | None
    execution_origin: Literal["qwen_live", "http_mock"] = "http_mock"
    real_qwen: Literal["not_run", "completed", "failed"] = "not_run"
    production_enabled: bool = False
    provenance: dict[str, object] | None = None
    tool_attempts: int
    model_requests: int
    max_tool_attempts: Literal[5] = 5
    max_model_requests: Literal[6] = 6
    result: ProfileInvestigationResult | InvestigationResult | None = None
    review_result_path: str | None = None
    expires_at: float


class ReviewSubmission(Model):
    snapshot_id: str = Field(min_length=1, max_length=128)
    fact_ids: tuple[str, ...] = Field(min_length=1, max_length=3)

    @field_validator("fact_ids", mode="before")
    @classmethod
    def json_ids(cls, value):
        return tuple(value) if isinstance(value, list) else value


class ReviewReceipt(Model):
    task_id: str
    batch_id: str
    snapshot_id: str
    result_id: str
    version: int
    candidate_ids: tuple[str, ...]
    fact_candidate_map: dict[str, str]
    agent_rule_version: str
    formal_rule_version: str
    status: Literal["pending_teacher_review"] = "pending_teacher_review"
    result_path: str
    review_path: str
    export_path: str
    notice: str = "候选已按正式规则重新计算；教师确认不等于因果证明。"


class TaskEvent(Model):
    """One recorded task phase; detail is a server-side whitelist, not the raw payload."""

    sequence: int
    phase: str
    at: str
    detail: dict[str, object]


class TaskEvents(Model):
    task_id: str
    events: tuple[TaskEvent, ...]
