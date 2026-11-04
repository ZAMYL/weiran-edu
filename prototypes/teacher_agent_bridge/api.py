"""Minimal test-host API using real teacher auth and the single Research runtime."""

import asyncio
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import uuid4

from app.ai.edu.discovery.models import AgentError, Limits
from app.core.errors import APIError
from app.core.exact_json import ExactJSONResponse
from app.edu.analysis_models import ExactRational, FilterCondition, GroupQuery
from app.edu.auth import Principal
from app.edu.discovery_disconnect import CancelCheck, cancellation
from app.edu.query import read_query, request_schema
from app.edu.review_models import AnalysisCreate
from app.routers.edu import Actor
from app.schemas.common import ErrorResponse
from fastapi import APIRouter, FastAPI, Request
from starlette.concurrency import run_in_threadpool

from prototypes.adaptive_assessment.analysis import VERSION, Kernel
from prototypes.adaptive_assessment.live.budget import CampaignBudget, OfflineSnapshotPermit
from prototypes.adaptive_assessment.live.pricing import RESEARCH_PRICE_20261104 as PRICE
from prototypes.adaptive_assessment.planners import AdaptiveFake
from prototypes.research_agent_v2.demo import mock_transport
from prototypes.research_agent_v2.goals import understand
from prototypes.research_agent_v2.runtime import investigate_database

from .models import (
    EvidenceLink,
    FactResult,
    InvestigationRequest,
    InvestigationResult,
    InvocationBudget,
    Metric,
    SourceReference,
    SourceRequest,
    SourceResult,
    ToolStep,
)
from .provider import ALIASES, DatabaseProvider, digest

OPS = {"<": "lt", "<=": "lte", "==": "eq", ">=": "gte", ">": "gt"}


def metrics(raw: object, prefix: str = "") -> list[Metric]:
    result = []
    if isinstance(raw, dict) and {"numerator", "denominator", "display"} == raw.keys():
        return [
            Metric(
                key=prefix,
                exact=ExactRational(numerator=raw["numerator"], denominator=raw["denominator"]),
                display=raw["display"],
            )
        ]
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in {"fact_id", "qualified", "ranking", "conditions", "exploration_thresholds"}:
                continue
            result.extend(metrics(value, (prefix + "." + key).strip(".")))
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            if isinstance(value, dict):
                result.extend(metrics(value, prefix + "." + value.get("item_id", str(index))))
    elif type(raw) in (int, float):
        result.append(Metric(key=prefix, number=raw, display=str(raw)))
    return result


def filter_conditions(query: dict) -> tuple[FilterCondition, ...]:
    return tuple(
        FilterCondition(
            dimension_code=ALIASES[p["item_id"]],
            operator=OPS[p["operator"]],
            value=float(p["threshold"]),
        )
        for p in query.get("conditions", [])
    )


class InvestigationHost:
    def __init__(self, provider: DatabaseProvider, state: Path):
        self.provider, self.state = provider, state
        self.runs = {}

    def execute(
        self,
        actor: Principal,
        batch_id: str,
        body: InvestigationRequest,
        cancelled: CancelCheck | None,
    ) -> InvestigationResult:
        source = self.provider.acquire(actor, batch_id, body.snapshot_id, cancelled)
        # Never send arbitrary question text. Existing mapper handles only controlled goals.
        question = body.question
        for alias, spec in zip(ALIASES, source.envelope.data.items, strict=True):
            question = question.replace(spec.name, alias)
        try:
            understand(question, Kernel(source.envelope).catalog())
        except ValueError:
            raise APIError(422, "AI_GOAL_UNSUPPORTED", "当前目标不受支持或缺少合法项目") from None
        campaign = "db-agent-" + digest((actor.teacher_id, batch_id, body.snapshot_id))[:40]
        permit = OfflineSnapshotPermit(source, campaign, PRICE.fingerprint)
        try:
            budget = CampaignBudget(permit, self.state, origin="http_mock", today=date(2026, 11, 4))
        except AgentError:
            raise APIError(
                409, "AI_CAMPAIGN_ALREADY_USED", "该合成调查额度已使用或不可用"
            ) from None
        try:
            if cancelled is not None and cancelled():
                raise APIError(499, "AI_CANCELLED", "调查请求已取消")
            raw, executor = asyncio.run(
                investigate_database(
                    source,
                    question,
                    budget,
                    transport=mock_transport(AdaptiveFake() if body.strategy == "fixed" else None),
                    limits=Limits(model_requests=6, tool_attempts=5, timeout_seconds=30),
                )
            )
            source.validate()
            if cancelled is not None and cancelled():
                raise APIError(499, "AI_CANCELLED", "调查请求已取消")
            run_id = str(uuid4())
            facts = []
            witness_by_id = {w.reference_id: w for w in source.envelope.material.witnesses}
            for f in executor.facts.values():
                executor._verify(f)
                self.provider.replay_witnesses(
                    source, [ref for s in f.item_sources for ref in s.references]
                )
                facts.append(
                    FactResult(
                        fact_id=f.fact_id,
                        batch_id=batch_id,
                        snapshot_id=body.snapshot_id,
                        method=f.tool,
                        rule_version=VERSION,
                        qualified=f.view["qualified"],
                        source_verified=True,
                        conditions=filter_conditions(f.query["arguments"]),
                        metrics=tuple(metrics(f.view)),
                        source_refs=tuple(
                            SourceReference(
                                metric_key=s.key,
                                dimension_code=ALIASES[s.item_id],
                                participant_count=len(s.participants),
                                witness_kind="row"
                                if any(witness_by_id[r].kind == "row" for r in s.references)
                                else "score",
                                records_path=f"/api/v1/edu/batches/{batch_id}/ai-investigations/{run_id}/records/query",
                                records_query=SourceRequest(
                                    snapshot_id=body.snapshot_id,
                                    fact_id=f.fact_id,
                                    metric_key=s.key,
                                ),
                            )
                            for s in f.item_sources
                        ),
                        limitation_codes=tuple(f.view.get("limitation_codes", ())),
                        exploration_query=GroupQuery(
                            snapshot_id=body.snapshot_id,
                            conditions=filter_conditions(f.query["arguments"]),
                        )
                        if f.tool == "compare_student_groups"
                        else None,
                        save_group_analysis_request=AnalysisCreate(
                            snapshot_id=body.snapshot_id,
                            title="AI调查后的群体核对",
                            candidate_ids=(),
                            conditions=filter_conditions(f.query["arguments"]),
                        )
                        if f.tool == "compare_student_groups"
                        else None,
                    )
                )
            source.validate()
            result = InvestigationResult(
                run_id=run_id,
                batch_id=batch_id,
                snapshot_id=body.snapshot_id,
                strategy=body.strategy,
                source_manifest_sha256=source.fingerprint,
                batch_record_count=len(source.envelope.data.participants),
                status=raw["status"],
                stop_reason=raw["stop_reason"],
                controlled_goal=raw["goal"]["type"],
                goal_items=tuple(ALIASES[i] for i in raw["goal"]["items"]),
                missing_evidence=tuple(raw["missing_evidence"]),
                facts=tuple(facts),
                selected_fact_ids=tuple(c["fact_id"] for c in raw["findings"]),
                retained_fact_ids=tuple(raw["retained_fact_ids"]),
                trace=tuple(
                    ToolStep(
                        tool=t["tool"],
                        round=t["round"],
                        phase=t["phase"],
                        status=t["status"],
                        fact_ids=tuple(t["fact_ids"]),
                    )
                    for t in raw["trace"]
                ),
                budget=InvocationBudget(
                    model_requests=raw["model_requests"],
                    tool_attempts=raw["tool_attempts"],
                    verification_replays=executor.replay_executions,
                ),
                uncovered_direction_count=len(
                    raw["investigation_coverage"]["uncovered_direction_ids"]
                ),
            )
            # Request cancellation belongs to this invocation, not future evidence reads.
            # Keep the immutable data proof and current-grant checks; never retain its event.
            source = replace(source, envelope=replace(source.envelope, cancelled=None))
            source.validate()
            executor.source = source
            executor.kernel = Kernel(source.envelope)
            self.runs[run_id] = (actor.teacher_id, source, executor, result)
            return result
        except AgentError:
            raise APIError(
                409, "AI_INVESTIGATION_BLOCKED", "调查预算、披露或事实校验未通过"
            ) from None
        finally:
            budget.close()

    def get(
        self, actor: Principal, batch_id: str, run_id: str, snapshot_id: str
    ) -> InvestigationResult:
        stored = self.runs.get(run_id)
        if stored is None or stored[0] != actor.teacher_id:
            raise APIError(404, "NOT_FOUND", "调查不存在或无权访问")
        _, source, _, result = stored
        if (batch_id, snapshot_id) != (result.batch_id, result.snapshot_id):
            raise APIError(409, "SNAPSHOT_MISMATCH", "调查批次或快照不一致")
        source.validate()
        return result

    def records(
        self, actor: Principal, batch_id: str, run_id: str, body: SourceRequest
    ) -> SourceResult:
        stored = self.runs.get(run_id)
        if stored is None or stored[0] != actor.teacher_id:
            raise APIError(404, "NOT_FOUND", "调查不存在或无权访问")
        _, source, executor, result = stored
        if (batch_id, body.snapshot_id) != (result.batch_id, result.snapshot_id):
            raise APIError(409, "SNAPSHOT_MISMATCH", "调查批次或快照不一致")
        source.validate()
        fact = executor.facts.get(body.fact_id)
        if fact is None:
            raise APIError(404, "NOT_FOUND", "调查事实不存在")
        executor._verify(fact)
        sources = [s for s in fact.item_sources if s.key == body.metric_key]
        if len(sources) != 1:
            raise APIError(404, "NOT_FOUND", "指标来源不存在")
        s = sources[0]
        # Check complete actual participation BEFORE pagination; no cohort-membership shortcut.
        links = []
        witnesses = self.provider.replay_witnesses(source, s.references)
        for pid, ref, witness in zip(s.participants, s.references, witnesses, strict=True):
            if witness.participant_id != pid or witness.item_id != s.item_id:
                raise APIError(503, "DATA_INTEGRITY_ERROR", "来源归属不一致")
            if witness.kind != "score":
                raise APIError(
                    422, "AI_ROW_WITNESS_NOT_SCORE_EVIDENCE", "行状态依据不可当作个人评分证据"
                )
            links.append(
                EvidenceLink(
                    student_id=pid,
                    evidence_id=ref,
                    dimension_code=ALIASES[s.item_id],
                    evidence_path=f"/api/v1/edu/batches/{batch_id}/evidence/{ref}",
                )
            )
        source.validate()
        return SourceResult(
            batch_id=batch_id,
            snapshot_id=body.snapshot_id,
            run_id=run_id,
            fact_id=body.fact_id,
            metric_key=body.metric_key,
            total_count=len(links),
            offset=body.offset,
            items=tuple(links[body.offset : body.offset + body.limit]),
        )


def install(application: FastAPI, provider: DatabaseProvider, state: Path) -> InvestigationHost:
    if application.state.settings.environment != "test" or type(provider) is not DatabaseProvider:
        raise ValueError("synthetic_integration_host_only")
    host = InvestigationHost(provider, state)
    router = APIRouter(
        prefix="/api/v1/edu/batches/{batch_id}",
        tags=["AI synthetic integration"],
        default_response_class=ExactJSONResponse,
        responses={
            code: {"model": ErrorResponse}
            for code in (401, 403, 404, 409, 413, 415, 422, 499, 500, 503)
        },
    )

    @router.post(
        "/ai-investigations",
        response_model=InvestigationResult,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": request_schema(InvestigationRequest)}},
            }
        },
    )
    async def investigate(batch_id: str, request: Request, actor: Actor):
        body = await read_query(request, InvestigationRequest)
        return await run_in_threadpool(host.execute, actor, batch_id, body, cancellation(request))

    @router.post(
        "/ai-investigations/{run_id}/records/query",
        response_model=SourceResult,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": request_schema(SourceRequest)}},
            }
        },
    )
    async def records(batch_id: str, run_id: str, request: Request, actor: Actor):
        body = await read_query(request, SourceRequest)
        return await run_in_threadpool(host.records, actor, batch_id, run_id, body)

    @router.get("/ai-investigations/{run_id}", response_model=InvestigationResult)
    def get_result(batch_id: str, run_id: str, snapshot_id: str, actor: Actor):
        return host.get(actor, batch_id, run_id, snapshot_id)

    application.include_router(router)
    application.state.investigation_host = host
    return host
