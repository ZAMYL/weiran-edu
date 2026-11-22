"""Explicit test-host wiring between the assessment task service and C-01."""

import asyncio
import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

import httpx
from app.ai.edu.discovery.models import AgentError, AgentRun, Limits
from app.core.errors import APIError
from app.edu.assessments import AssessmentReader
from app.edu.auth import Principal
from app.edu.programming_tasks import (
    CAP_MODEL_REQUESTS,
    CAP_TOOL_ATTEMPTS,
    PURPOSE_MOCK,
    FindingEvidence,
    ProgrammingFinding,
    ProgrammingResult,
    ProgrammingTaskService,
)

from prototypes.adaptive_assessment.data import Witness
from prototypes.adaptive_assessment.live.budget import (
    CampaignBudget,
    OfflineProgrammingPermit,
)
from prototypes.adaptive_assessment.live.demo import MOCK_DATE
from prototypes.adaptive_assessment.live.pricing import PRICE

from .database_evidence import AssessmentEvidenceProvider
from .programming import ProgrammingExecutor, investigate_programming
from .programming_evidence import EvidenceScope, EvidenceSnapshot, ProgrammingSource


@dataclass(frozen=True, repr=False)
class _TaskEvidenceProvider(AssessmentEvidenceProvider):
    guard: Callable[[], bool]

    def capture(self, scope: EvidenceScope) -> EvidenceSnapshot:
        self.guard()
        snapshot = super().capture(scope)
        self.guard()
        return snapshot

    def require_details(self, scope: EvidenceScope) -> None:
        self.guard()
        super().require_details(scope)


class _TaskCampaign(CampaignBudget):
    def __init__(
        self,
        service: ProgrammingTaskService,
        scope: EvidenceScope,
        permit: OfflineProgrammingPermit,
    ) -> None:
        self.service = service
        self.task_id = scope.task
        self.tool_attempts = 0
        super().__init__(
            permit,
            service.database.path.resolve().parent / "programming-campaigns",
            origin="http_mock",
            today=MOCK_DATE,
        )

    def reserve(self) -> int:
        ticket = super().reserve()
        self.mirror(self.tool_attempts)
        return ticket

    def audit_tool(
        self, phase: str, executor: ProgrammingExecutor, _entry: dict
    ) -> None:
        # Durable tool audit straight from the real executor entry. Only opaque
        # tool/decision identifiers are stored: never code, names, paths or prompts.
        # Persist before the budget mirror so a revoked/cancelled task still keeps
        # the attempt record that explains why execution stopped.
        self.record(
            "tool_audit",
            {
                "tool": _entry["tool"],
                "attempt": len(executor.attempt_ledger),
                "phase": phase,
                "status": _entry["status"],
                "arguments": {
                    key: _entry["arguments"][key]
                    for key in ("overview_id", "direction")
                    if key in _entry["arguments"]
                },
                "fact_ids": _entry.get("fact_ids", []),
                "error": _entry.get("error"),
            },
        )
        if phase == "attempt":
            self.mirror(len(executor.attempt_ledger))

    def record(self, phase: str, payload: dict) -> None:
        """Reuse the task service's existing public event writer, unchanged."""
        with self.service.database.transaction(write=True) as connection:
            self.service.event(connection, self.task_id, phase, payload)

    def reconcile(self, run: AgentRun) -> None:
        """Server task row, campaign journal and executor session must agree."""
        with self.service.database.transaction() as connection:
            row = connection.execute(
                "SELECT budget_json,tool_attempts FROM ai_programming_tasks WHERE task_id=?",
                (self.task_id,),
            ).fetchone()
        budget = json.loads(row["budget_json"])
        snapshot = self.snapshot()
        if not (
            budget["request_count"] == run.model_requests == snapshot["request_count"]
            and budget["tool_attempts"] == row["tool_attempts"] == run.tool_attempts
        ):
            raise AgentError("programming_budget_audit_mismatch")

    def mirror(self, attempts: int) -> None:
        self.service.budget(
            self.task_id,
            request_count=self.snapshot()["request_count"],
            tool_attempts=attempts,
        )
        self.tool_attempts = attempts


def install_offline_runner(
    service: ProgrammingTaskService, transport: httpx.MockTransport
) -> None:
    """Bind a server-owned transport on a test host before accepting tasks."""
    if service.environment != "test" or type(transport) is not httpx.MockTransport:
        raise ValueError("offline_programming_test_host_required")
    if service.runner is not None:
        raise ValueError("programming_runner_already_installed")

    def run(
        task_service: ProgrammingTaskService,
        task_id: str,
        row: dict,
        actor: Principal,
        guard: Callable[[], bool],
    ) -> ProgrammingResult:
        if task_service is not service or row["purpose"] != PURPOSE_MOCK:
            raise APIError(409, "AI_LIVE_NOT_ENABLED", "编程调查未启用真实模型")
        return asyncio.run(_investigate(service, task_id, row, actor, guard, transport))

    service.runner = run


async def _investigate(
    service: ProgrammingTaskService,
    task_id: str,
    row: dict,
    actor: Principal,
    guard: Callable[[], bool],
    transport: httpx.MockTransport,
) -> ProgrammingResult:
    scope = EvidenceScope(
        row["teacher_id"], task_id, row["assessment_id"], row["snapshot_id"]
    )
    reader = AssessmentReader(service.database, originals=service.originals)
    provider = _TaskEvidenceProvider(reader, actor, scope, guard)
    campaign = None
    try:
        source = ProgrammingSource(scope, provider)

        async def guarded_send(request: httpx.Request) -> httpx.Response:
            source.validate()
            return await transport.handle_async_request(request)

        permit = OfflineProgrammingPermit(
            source,
            row["campaign_id"],
            PRICE.fingerprint,
            max_requests=CAP_MODEL_REQUESTS,
        )
        campaign = _TaskCampaign(service, scope, permit)
        try:
            guard()
            run, executor = await investigate_programming(
                source,
                source.material.problems,
                campaign,
                transport=httpx.MockTransport(guarded_send),
                limits=Limits(
                    model_requests=CAP_MODEL_REQUESTS,
                    tool_attempts=CAP_TOOL_ATTEMPTS,
                    run_seconds=min(90, row["deadline"] - service.clock()),
                ),
            )
            # Persist the real session outcome before any further budget call can
            # raise: completed, insufficient-evidence and failed runs all stay auditable.
            campaign.record(
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
            # The session also counts malformed tool attempts rejected before executor entry.
            campaign.mirror(run.tool_attempts)
            campaign.reconcile(run)
            if run.status != "completed":
                if run.reason in {"model_timeout", "run_timeout"}:
                    raise APIError(410, "AI_TASK_TIMEOUT", "编程调查已超时")
                if run.reason == "tool_budget_exhausted":
                    raise APIError(
                        409, "AI_TOOL_BUDGET_EXHAUSTED", "工具读取预算已用尽"
                    )
                if run.reason in {"model_budget_exhausted", "authorized_request_limit"}:
                    raise APIError(409, "AI_BUDGET_EXHAUSTED", "模型请求预算已用尽")
                raise AgentError("programming_investigation_failed")
            result = _project(run, executor, provider)
            source.validate()
            return result
        finally:
            campaign.close()
    except AgentError as error:
        if campaign is not None:
            # 审计回写失败不得掩盖原始拒绝语义。
            with suppress(Exception):
                campaign.record("run_aborted", {"code": str(error)})
        raise APIError(
            409, "AI_PROGRAMMING_EVIDENCE_UNAVAILABLE", "编程调查未取得可核验证据"
        ) from None


def _anchor(witness: Witness, students: dict[str, str]) -> FindingEvidence:
    entry, separator, location = witness.locator.rpartition("#block/")
    index, _, field = location.partition("/")
    if not separator or not index.isdecimal() or witness.participant_id not in students:
        raise AgentError("invalid_programming_witness")
    source_row = test_index = None
    if witness.kind == "test" and field.startswith("result/"):
        source_row = int(field.removeprefix("result/"))
        point, _ = json.loads(witness.value)
        test_index = int(point)
    elif witness.kind != "verdict" or field != "verdict":
        raise AgentError("invalid_programming_witness")
    return FindingEvidence(
        asset_id=witness.source_id,
        source_entry=entry,
        student_key=students[witness.participant_id],
        problem_label=witness.item_id,
        source_index=int(index),
        source_row=source_row,
        test_index=test_index,
    )


def _project(
    run: AgentRun, executor: ProgrammingExecutor, provider: _TaskEvidenceProvider
) -> ProgrammingResult:
    scope = provider.scope
    provider.require_details(scope)
    overview = provider.reader.overview(provider.principal, scope.batch)
    students = {}
    if run.selected_ids:
        people = provider.reader.participants(provider.principal, scope.batch).items
        students = {person.student_pk: person.external_key for person in people}
    findings = []
    for identifier, card in zip(run.selected_ids, run.cards, strict=True):
        direction = card["direction"]
        evidence = executor.resolve(identifier, direction["direction"] + ".affected")
        if not evidence or len(evidence) != direction["affected"]:
            raise AgentError("invalid_programming_witness")
        point = card["test_point"]
        where = f"测试点 {point}" if point is not None else "整题判题"
        statement = (
            f"{where}：{direction['affected']} / {direction['covered']} 条覆盖记录"
            "出现该现象。根因有待核查。"
        )
        if card["features"]:
            details = "、".join(
                f"{feature['feature']} {feature['present']} / {feature['checked']}"
                for feature in card["features"]
            )
            statement += f"已核验的代码特征命中 / 可测记录为 {details}。"
        findings.append(
            ProgrammingFinding(
                finding_id=identifier,
                code=direction["phenomenon"],
                statement=statement,
                evidence=[_anchor(witness, students) for witness in evidence],
            )
        )
    return ProgrammingResult(
        assessment_id=scope.batch,
        source_snapshot_id=scope.snapshot,
        asset_sha256=[asset.sha256 for asset in overview.assets],
        statement="编程调查已完成。"
        if findings
        else "当前证据未支持可披露的编程发现。",
        findings=findings,
    )
