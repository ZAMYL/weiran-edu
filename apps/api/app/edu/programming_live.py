"""LIVE-02：正式编程任务的真实 Qwen 受控接线。

本模块只做三件事：把 Codex 的 C-01 执行骨架接到正式任务行；把 CampaingBudget
换成服务端独占账本；把真实执行来源写入同一个任务数据库。它不改变 Planner、证据
Provider 或前端契约，也不从请求、查询参数或客户端字段读取任何模型凭证、审批路径或
来源指纹。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import Literal

import httpx
from app.ai.edu.discovery.models import AgentError, AgentRun, Limits, Strict
from app.ai.edu.qwen_config import QwenSettings
from app.core.errors import APIError
from pydantic import Field, field_validator

from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.pricing import reviewed_price
from prototypes.qwen_execution.runtime import code_fingerprint
from prototypes.research_agent_v2.programming import (
    ProgrammingExecutor,
    ProgrammingPlanner,
    _run_programming,
)
from prototypes.research_agent_v2.programming_evidence import (
    EvidenceScope,
    ProgrammingSource,
)
from prototypes.research_agent_v2.programming_task_adapter import (
    _TaskEvidenceProvider,
    _project,
)

from app.edu.assessments import AssessmentReader
from app.edu.auth import Principal
from app.edu.programming_tasks import (
    CAP_MODEL_REQUESTS,
    CAP_TOOL_ATTEMPTS,
    PURPOSE_LIVE,
    ProgrammingResult,
    ProgrammingTaskService,
)

APPROVAL_ENV = "WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL"
LEDGER_ROOT_ENV = "WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT"
REPOSITORY = Path(__file__).resolve().parents[4]


def _inside_repository(path: Path) -> bool:
    return path.resolve().is_relative_to(REPOSITORY)


def _outside_repository(raw: str, name: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise AgentError(f"{name.lower()}_must_be_absolute")
    resolved = path.resolve()
    if _inside_repository(resolved):
        raise AgentError(f"{name.lower()}_must_stay_outside_repository")
    return resolved


class ProgrammingLiveApproval(Strict):
    """Operator-issued authority; its JSON file never reaches the browser."""

    campaign_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,64}$")
    human_approval_reference: str = Field(pattern=r"^[a-zA-Z0-9:_-]{8,120}$")
    purpose: Literal["programming_live"]
    model: Literal["qwen3.8-flash"]
    endpoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    # assessment_id -> exhausted source snapshot id. Only this exact pair may run.
    source_fingerprints: dict[str, str] = Field(min_length=1, max_length=16)
    price_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    valid_until: str
    max_requests: int = Field(ge=1, le=6)
    max_output_tokens: int = Field(ge=1, le=800)
    total_token_limit: int = Field(ge=1, le=10_000_000)
    cost_cap_cny: Decimal = Field(gt=0, le=100)
    independent_review_reference: str = Field(min_length=8, max_length=200)
    reviewed_code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("source_fingerprints")
    @classmethod
    def _registered_sources(cls, value: dict[str, str]):
        for assessment, snapshot in value.items():
            if not isinstance(assessment, str) or not assessment.strip():
                raise ValueError("programming_assessment_id_empty")
            if not re.fullmatch(r"[a-f0-9]{64}", snapshot):
                raise ValueError("programming_snapshot_fingerprint_invalid")
        return value

    def check(self, today: date) -> None:
        try:
            expiry = date.fromisoformat(self.valid_until)
        except ValueError:
            raise AgentError("invalid_authorization_expiry") from None
        price = reviewed_price(self.price_fingerprint)
        if (
            expiry < today
            or expiry > date.fromisoformat(price.valid_until)
            or today < date.fromisoformat(price.checked_on)
            or price.model != self.model
            or self.reviewed_code_sha256 != code_fingerprint()
        ):
            raise AgentError("stale_authorization_or_price")
        if (
            not 3 <= self.max_requests <= CAP_MODEL_REQUESTS
            or not 1 <= self.max_output_tokens <= 800
            or not 1 <= self.total_token_limit <= 10_000_000
            or not Decimal(0) < self.cost_cap_cny <= Decimal(100)
        ):
            raise AgentError("invalid_programming_live_budget")
        for _assessment, snapshot in self.source_fingerprints.items():
            if not re.fullmatch(r"[a-f0-9]{64}", snapshot):
                raise AgentError("programming_source_fingerprint_invalid")


@dataclass(frozen=True)
class ProgrammingLiveAdmission:
    approval: ProgrammingLiveApproval
    ledger_root: Path

    @classmethod
    def load(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        today: date | None = None,
    ) -> ProgrammingLiveAdmission:
        env = os.environ if environ is None else environ
        raw = env.get(APPROVAL_ENV)
        if not raw:
            raise AgentError("programming_live_approval_required")
        path = _outside_repository(raw, "WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL")
        try:
            payload = path.read_bytes()
        except OSError:
            raise AgentError("programming_live_approval_unreadable") from None
        if not payload or len(payload) > 8192:
            raise AgentError("programming_live_approval_size_invalid")
        try:
            approval = ProgrammingLiveApproval.model_validate_json(payload)
        except ValueError:
            raise AgentError("programming_live_approval_invalid") from None
        approval.check(today or date.today())
        root = _outside_repository(
            env.get(LEDGER_ROOT_ENV, ""), "WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT"
        )
        return cls(approval=approval, ledger_root=root)

    def require_source(self, assessment_id: str, snapshot_id: str) -> None:
        if self.approval.source_fingerprints.get(assessment_id) != snapshot_id:
            raise AgentError("programming_source_not_approved")

    def require_scope(self, scope: EvidenceScope) -> None:
        self.require_source(scope.batch, scope.snapshot)


def _audit_budget(snapshot: dict, tool_attempts: int) -> dict:
    """Stable server-side summary; no prompt, code, secret or client input."""
    entries = []
    for entry in snapshot.get("entries", []):
        entries.append(
            {
                key: entry.get(key)
                for key in (
                    "request",
                    "state",
                    "http_status",
                    "transport_status",
                    "error",
                    "actual_usage",
                )
                if entry.get(key) is not None
            }
        )
    return {
        "campaign_id": snapshot["campaign_id"],
        "origin": snapshot["origin"],
        "request_count": snapshot["request_count"],
        "tool_attempts": tool_attempts,
        "charged_tokens": snapshot["charged_tokens"],
        "charged_cny_upper_bound": snapshot["charged_cny_upper_bound"],
        "max_requests": snapshot["max_requests"],
        "total_token_limit": snapshot["total_token_limit"],
        "cost_cap_cny": snapshot["cost_cap_cny"],
        "price_fingerprint": snapshot["price_fingerprint"],
        "blocked": snapshot["blocked"],
        "entries": entries,
    }


class ProgrammingLiveBudget(CampaignBudget):
    """One server-owned campaign journal, bound to one formal task row."""

    def __init__(
        self,
        admission: ProgrammingLiveAdmission,
        service: ProgrammingTaskService,
        task_id: str,
        source: ProgrammingSource,
        *,
        authorize: Callable[[], bool],
        transport: httpx.MockTransport | None = None,
    ):
        if type(admission) is not ProgrammingLiveAdmission:
            raise AgentError("programming_live_admission_required")
        if type(service) is not ProgrammingTaskService or not callable(authorize):
            raise AgentError("programming_task_service_required")
        if type(source) is not ProgrammingSource:
            raise AgentError("programming_live_source_required")
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise AgentError("invalid_execution_transport")
        self.service = service
        self.task_id = task_id
        self.authorize = authorize
        self.transport = transport
        self.tool_attempts = 0
        bound = admission.approval.model_copy(
            update={"source_fingerprints": {source.recipe: source.fingerprint}}
        )
        self._initialize(bound, None, origin="qwen_live")

    def _check_authorization(self) -> None:
        # A revocation, credential expiry or deadline must stop the next model
        # request even if the remote model is already in flight.
        self.authorize()
        super()._check_authorization()

    def check(self, settings: QwenSettings, source: ProgrammingSource) -> None:
        if type(source) is not ProgrammingSource:
            raise AgentError("programming_live_source_required")
        super().check(settings, source)

    def _persist(self) -> None:
        super()._persist()
        snapshot = self.snapshot()
        # Database accounting is monotonic and server-owned; journal is written
        # first so a crash cannot hide a reservation that the model later uses.
        self.service.budget(
            self.task_id,
            request_count=snapshot["request_count"],
            tool_attempts=self.tool_attempts,
        )
        self.service.record_provenance(
            self.task_id, {"budget": _audit_budget(snapshot, self.tool_attempts)}
        )

    def mirror(self, attempts: int) -> None:
        self.service.budget(
            self.task_id,
            request_count=self.snapshot()["request_count"],
            tool_attempts=attempts,
        )
        self.tool_attempts = attempts

    def reserve(self) -> int:
        ticket = super().reserve()
        self.mirror(self.tool_attempts)
        return ticket

    def audit_tool(self, phase: str, executor: ProgrammingExecutor, entry: dict) -> None:
        payload = {
            "tool": entry["tool"],
            "attempt": len(executor.attempt_ledger),
            "phase": phase,
            "status": entry["status"],
            "arguments": {
                key: entry["arguments"][key]
                for key in ("overview_id", "direction")
                if key in entry["arguments"]
            },
            "fact_ids": entry.get("fact_ids", []),
            "error": entry.get("error"),
        }
        self.service.audit_tool(self.task_id, payload)
        if phase == "attempt":
            self.mirror(len(executor.attempt_ledger))

    def reconcile(self, run: AgentRun) -> None:
        with self.service.database.transaction() as connection:
            row = connection.execute(
                "SELECT budget_json,tool_attempts FROM ai_programming_tasks WHERE task_id=?",
                (self.task_id,),
            ).fetchone()
        if row is None:
            raise AgentError("programming_budget_audit_mismatch")
        budget = json.loads(row["budget_json"])
        snapshot = self.snapshot()
        if not (
            budget["request_count"] == run.model_requests == snapshot["request_count"]
            and budget["tool_attempts"] == row["tool_attempts"] == run.tool_attempts
        ):
            raise AgentError("programming_budget_audit_mismatch")


class ProgrammingLivePlanner(ProgrammingPlanner):
    """Only the admission override differs; Codex's decisions/tools stay intact."""

    def _admit_execution(self, budget, transport, allow_network):
        if type(budget) is not ProgrammingLiveBudget:
            raise AgentError("programming_live_budget_required")
        if budget.transport is not transport:
            raise AgentError("execution_transport_mismatch")
        if allow_network != (transport is None):
            raise AgentError("execution_origin_mismatch")
        budget._check_authorization()


def _record(service: ProgrammingTaskService, task_id: str, phase: str, payload: dict) -> None:
    with service.database.transaction(write=True) as connection:
        service.event(connection, task_id, phase, payload)


def _failure_code(error: AgentError) -> APIError:
    reason = str(error)
    if reason in {"model_timeout", "run_timeout"}:
        return APIError(410, "AI_TASK_TIMEOUT", "编程调查已超时")
    if reason == "tool_budget_exhausted":
        return APIError(409, "AI_TOOL_BUDGET_EXHAUSTED", "工具读取预算已用尽")
    if reason in {"model_budget_exhausted", "authorized_request_limit", "authorized_token_limit"}:
        return APIError(409, "AI_BUDGET_EXHAUSTED", "模型请求预算已用尽")
    if reason in {"authorized_cost_limit"}:
        return APIError(409, "AI_BUDGET_EXHAUSTED", "模型费用预算已用尽")
    if reason in {"stale_authorization_or_price", "invalid_authorization_expiry"}:
        return APIError(409, "AI_LIVE_APPROVAL_STALE", "真实执行审批已过期或价格已变化")
    if reason in {"reviewed_code_changed"}:
        return APIError(409, "AI_LIVE_CODE_CHANGED", "执行代码与独立审查指纹不一致")
    if reason in {"campaign_already_consumed", "approval_already_bound"}:
        return APIError(409, "AI_CAMPAIGN_ALREADY_USED", "该调查授权已被使用")
    if reason == "programming_source_not_approved":
        return APIError(409, "AI_LIVE_SOURCE_NOT_APPROVED", "该来源未包含在独立审批中")
    if reason in {"programming_budget_audit_mismatch"}:
        return APIError(503, "AI_BUDGET_AUDIT_MISMATCH", "预算账本与任务记录不一致")
    if reason in {"unverifiable_usage", "budget_journal_failed"}:
        return APIError(503, "AI_LIVE_USAGE_UNVERIFIED", "模型用量无法核验，已保留预留额度")
    return APIError(409, "AI_PROGRAMMING_EVIDENCE_UNAVAILABLE", "编程调查未取得可核验证据")


async def _investigate_live(
    service: ProgrammingTaskService,
    task_id: str,
    row: dict,
    actor: Principal,
    guard: Callable[[], bool],
    admission: ProgrammingLiveAdmission,
    settings: QwenSettings,
    transport: httpx.MockTransport | None,
) -> ProgrammingResult:
    scope = EvidenceScope(row["teacher_id"], task_id, row["assessment_id"], row["snapshot_id"])
    admission.require_scope(scope)
    reader = AssessmentReader(service.database, originals=service.originals)
    provider = _TaskEvidenceProvider(reader, actor, scope, guard)
    budget = None
    try:
        source = ProgrammingSource(scope, provider)
        admission.require_source(scope.batch, source.fingerprint)
        # This row is the execution provenance; it is written before the first
        # network call so a later failure cannot be mistaken for "never ran".
        service.record_provenance(
            task_id,
            {
                "execution_origin": "qwen_live",
                "real_qwen": "not_run",
                "production_enabled": True,
                "model": settings.model,
                "endpoint_sha256": sha256(
                    settings.base_url.rstrip("/").encode()
                ).hexdigest(),
                "approval_reference": admission.approval.human_approval_reference,
                "independent_review_reference": admission.approval.independent_review_reference,
                "approval_campaign_id": admission.approval.campaign_id,
                "approval_valid_until": admission.approval.valid_until,
                "approved_snapshot_id": scope.snapshot,
                "reviewed_code_sha256": admission.approval.reviewed_code_sha256,
            },
        )
        budget = ProgrammingLiveBudget(
            admission,
            service,
            task_id,
            source,
            authorize=guard,
            transport=transport,
        )
        guard()
        run, executor = await _run_programming(
            source,
            source.material.problems,
            budget,
            limits=Limits(
                model_requests=min(CAP_MODEL_REQUESTS, admission.approval.max_requests),
                tool_attempts=CAP_TOOL_ATTEMPTS,
                run_seconds=min(90, row["deadline"] - service.clock()),
            ),
            planner_factory=partial(
                ProgrammingLivePlanner,
                transport=transport,
                allow_network=transport is None,
                settings=settings,
            ),
        )
        _record(
            service,
            task_id,
            "run_summary",
            {
                "status": run.status,
                "reason": run.reason,
                "model_requests": run.model_requests,
                "tool_attempts": run.tool_attempts,
                "selected_fact_ids": list(run.selected_ids),
                "finding_count": len(run.cards),
            },
        )
        budget.mirror(run.tool_attempts)
        budget.reconcile(run)
        if run.status != "completed":
            if run.reason in {"model_timeout", "run_timeout"}:
                raise APIError(410, "AI_TASK_TIMEOUT", "编程调查已超时")
            if run.reason == "tool_budget_exhausted":
                raise APIError(409, "AI_TOOL_BUDGET_EXHAUSTED", "工具读取预算已用尽")
            if run.reason in {
                "model_budget_exhausted",
                "authorized_request_limit",
                "authorized_token_limit",
                "authorized_cost_limit",
            }:
                raise APIError(409, "AI_BUDGET_EXHAUSTED", "模型请求预算已用尽")
            raise AgentError("programming_investigation_failed")
        result = _project(run, executor, provider)
        source.validate()
        service.record_provenance(
            task_id,
            {
                "real_qwen": "completed",
                "model_requests": run.model_requests,
                "tool_attempts": run.tool_attempts,
                "result_summary": {
                    "finding_count": len(result.findings),
                    "statement": result.statement,
                },
            },
        )
        return result
    except APIError:
        raise
    except AgentError as error:
        with suppress(Exception):
            _record(service, task_id, "run_aborted", {"code": str(error)})
        raise _failure_code(error) from None
    except Exception:
        with suppress(Exception):
            _record(service, task_id, "run_aborted", {"code": "programming_live_execution_failed"})
        raise APIError(503, "AI_LIVE_MODEL_FAILED", "真实模型执行失败") from None
    finally:
        if budget is not None:
            budget.close()


def install_live_runner(
    service: ProgrammingTaskService,
    *,
    admission: ProgrammingLiveAdmission | None = None,
    settings: QwenSettings | None = None,
    transport: httpx.MockTransport | None = None,
) -> None:
    """Install the real runner only on the production task service.

    `admission` and `transport` are test-only server-side injections; neither is
    read from an HTTP request, query string or client JSON body.
    """
    if service.environment != "production":
        raise ValueError("programming_live_runner_requires_production")
    if service.runner is not None:
        raise ValueError("programming_runner_already_installed")
    if transport is not None and type(transport) is not httpx.MockTransport:
        raise ValueError("programming_live_test_transport_required")
    settings = settings or QwenSettings.from_env()
    if settings.model != "qwen3.8-flash":
        raise ValueError("programming_live_model_not_reviewed")
    reload_approval = admission is None
    admission = admission or ProgrammingLiveAdmission.load()
    if type(admission) is not ProgrammingLiveAdmission:
        raise ValueError("programming_live_admission_required")

    def run(
        task_service: ProgrammingTaskService,
        task_id: str,
        row: dict,
        actor: Principal,
        guard: Callable[[], bool],
    ) -> ProgrammingResult:
        if task_service is not service or row["purpose"] != PURPOSE_LIVE:
            raise APIError(409, "AI_LIVE_NOT_ENABLED", "编程调查未启用真实模型")
        # Re-read the operator file, ledger root and code fingerprint per task:
        # deleting or editing an approval while the HTTP process is running must
        # stop the next execution instead of trusting a startup snapshot.
        current = ProgrammingLiveAdmission.load() if reload_approval else admission
        try:
            return asyncio.run(
                _investigate_live(
                    service,
                    task_id,
                    row,
                    actor,
                    guard,
                    current,
                    settings,
                    transport,
                )
            )
        except APIError:
            raise
        except AgentError as error:
            raise _failure_code(error) from None
        except Exception:
            raise APIError(503, "AI_LIVE_MODEL_FAILED", "真实模型执行失败") from None

    service.runner = run
