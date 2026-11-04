"""Durable task DTOs. No client statistics, facts, consent flags or budgets."""

from typing import Literal

from app.edu.models import Model
from pydantic import Field, field_validator

from prototypes.teacher_agent_bridge.models import InvestigationRequest, InvestigationResult


class TaskCreate(InvestigationRequest):
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class TaskView(Model):
    task_id: str
    batch_id: str
    snapshot_id: str
    campaign_id: str
    state: str
    error_code: str | None
    stop_reason: str | None
    execution_origin: Literal["http_mock"] = "http_mock"
    real_qwen: Literal["not_run"] = "not_run"
    production_enabled: Literal[False] = False
    tool_attempts: int
    model_requests: int
    max_tool_attempts: Literal[5] = 5
    max_model_requests: Literal[6] = 6
    result: InvestigationResult | None = None
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
