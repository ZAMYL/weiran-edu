"""Reuse #70 projection and #66 executor, backed by SQLite proof/disclosure journals."""

import asyncio
import json
from datetime import date
from hashlib import sha256
from pathlib import Path

from prototypes.adaptive_assessment.analysis import VERSION
from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.research_agent_v2.runtime import ResearchExecutor
from prototypes.teacher_agent_bridge.api import InvestigationHost
from prototypes.teacher_agent_bridge.models import (
    InvestigationRequest,
    SourceRequest,
    SourceResult,
)
from prototypes.teacher_agent_bridge.provider import DatabaseEnvelope, DatabaseSource, digest

from app.ai.edu.discovery.models import AgentError, Limits
from app.core.errors import APIError
from app.edu.auth import Principal

from .models import ProfileInvestigationResult, TaskView
from .profiles import extend_proof, investigate_profiles, parse_result, replay_profiles
from .store import TaskStore, blocked


def proof(executor: ResearchExecutor) -> dict:
    return {
        "executor_run_id": executor.run_id,
        "rule_version": VERSION,
        "queries": executor.attempt_ledger,
        "facts": {
            f.fact_id: {"view_sha": f.view_sha256, "proof_sha": f.proof_sha256}
            for f in executor.facts.values()
        },
        "released": [sorted(group) for group in executor.released],
        "output_bytes": executor.output_bytes,
    }


class DurableBudget(CampaignBudget):
    def __init__(self, permit, directory, store, task_id):
        self.store, self.task_id = store, task_id
        try:
            super().__init__(permit, directory, origin="http_mock", today=date(2026, 11, 4))
        except BaseException:
            if hasattr(self, "_fd"):
                self.close()
            raise

    def _persist(self):
        super()._persist()  # Preserve exclusive, fsync-before-request campaign journal.
        self.store.budget(self.task_id, self.snapshot())

    def audit_tool(self, phase, executor, entry):
        self.store.audit(self.task_id, phase, executor, entry)


class Invocation(InvestigationHost):
    def __init__(self, service, row):
        super().__init__(service.provider, service.directory)
        self.service, self.row = service, row

    def acquire(self, actor, batch_id, snapshot_id, cancelled):
        return self.service.source(self.row, actor, running=True)

    def campaign(self, actor, batch_id, snapshot_id):
        return self.row["campaign_id"]

    def result_id(self):
        return self.row["task_id"]

    def open_budget(self, permit):
        return DurableBudget(permit, self.state, self.service.store, self.row["task_id"])

    def investigate(self, source, question, budget, body):
        from prototypes.research_agent_v2.goals import understand

        goal = understand(
            question, {"items": [{"item_id": i.item_id} for i in source.envelope.data.items]}
        )
        if body.strategy != "adaptive" or goal.kind != "priority" or goal.items:
            return super().investigate(source, question, budget, body)
        self.profile_active = True
        raw, executor, self.profile_projection, self.profile_staged = asyncio.run(
            investigate_profiles(source, question, budget, self.service.profile_limits)
        )
        return raw, executor

    def completed(self, actor, source, executor, result):
        if result.status != "completed":
            with self.service.store.database.transaction(write=True) as c:
                self.service.store.event(
                    c,
                    self.row["task_id"],
                    "failed_execution_trace",
                    {
                        "trace": [t.model_dump(mode="json") for t in result.trace],
                        "stop_reason": result.stop_reason,
                    },
                )
            code = (
                "AI_PROFILE_" + result.stop_reason.upper()
                if getattr(self, "profile_active", False)
                else "AI_EXECUTION_INCOMPLETE"
            )
            raise APIError(409, code, "调查未正常完成，未发布部分结果")
        base = (
            f"/api/v1/edu/batches/{result.batch_id}"
            f"/ai-investigation-tasks/{result.run_id}/records/query"
        )
        facts = tuple(
            f.model_copy(
                update={
                    "source_refs": tuple(
                        s.model_copy(update={"records_path": base}) for s in f.source_refs
                    )
                }
            )
            for f in result.facts
        )
        result = result.model_copy(
            update={
                "facts": facts,
                "report_integration": "eligible_facts_require_formal_recomputation",
            }
        )
        verified = proof(executor)
        if getattr(self, "profile_projection", None) is not None:
            result = ProfileInvestigationResult(
                **result.model_dump(), profile_projection=self.profile_projection
            )
            verified = extend_proof(
                verified, result, executor, self.profile_staged, self.service.profile_limits
            )
        self.service.store.finish(self.row["task_id"], actor, result, verified)


class AuthorizedEnvelope(DatabaseEnvelope):
    def validate(self):
        super().validate()
        # A potentially expensive DB capture must not be the final operation after
        # a stale purpose check. Recheck immediately after it, before any disclosure.
        if self.cancelled is not None:
            self.cancelled()


class TaskService:
    def __init__(self, provider, store: TaskStore, directory: Path, *, profile_limits=None):
        self.provider, self.store, self.directory = provider, store, directory
        self.profile_limits = profile_limits or Limits(
            model_requests=6, tool_attempts=5, timeout_seconds=30
        )
        if self.profile_limits.model_requests > 6 or self.profile_limits.tool_attempts > 5:
            raise ValueError("profile_limits_exceed_existing_task_budget")

    def source(self, row: dict, actor: Principal, *, running: bool = False) -> DatabaseSource:
        def current_authorization():
            return self.store.authorize(row["task_id"], actor, running=running)

        current_authorization()
        material, manifest = self.provider.capture(actor, row["batch_id"], row["snapshot_id"])
        if sha256(manifest).hexdigest() != row["source_sha"]:
            blocked("AI_SOURCE_CONFLICT")
        return DatabaseSource(
            "authorized-db-" + row["batch_id"],
            AuthorizedEnvelope(
                material,
                manifest,
                "authorized_database",
                self.provider,
                actor,
                row["batch_id"],
                row["snapshot_id"],
                current_authorization,
            ),
            row["source_sha"],
        )

    def execute(self, task_id: str) -> None:
        try:
            row = self.store.start(task_id)
        except APIError:
            # Another worker/cancellation may already own this task. A failed claim
            # must never mutate the live owner's state or its consumed budget.
            return
        try:
            actor = Principal(row["teacher_id"], row["credential_sha"])
            body = json.loads(row["request_json"])
            body.pop("idempotency_key")
            body.pop("_result_contract", None)
            Invocation(self, row).execute(
                actor,
                row["batch_id"],
                InvestigationRequest.model_validate(body),
                lambda: self.store.guard(task_id),
            )
        except APIError as error:
            self.store.fail(task_id, error.code)
        except (AgentError, ValueError):
            self.store.fail(task_id, "AI_INVESTIGATION_BLOCKED")
        except Exception:
            # No partial result or sensitive exception message crosses the API boundary.
            self.store.fail(task_id, "AI_TASK_FAILED")

    def row(self, actor: Principal, batch_id: str, task_id: str, snapshot_id: str) -> dict:
        row = self.store.get(actor, task_id)
        if row["batch_id"] != batch_id or row["snapshot_id"] != snapshot_id:
            blocked("SNAPSHOT_MISMATCH")
        self.source(row, actor).validate()
        return row

    def view(self, actor: Principal, batch_id: str, task_id: str, snapshot_id: str) -> TaskView:
        row = self.row(actor, batch_id, task_id, snapshot_id)
        result, _ = self.verified_result(actor, row)
        receipt = json.loads(row["review_json"]) if row["review_json"] else None
        return TaskView(
            task_id=task_id,
            batch_id=batch_id,
            snapshot_id=snapshot_id,
            campaign_id=row["campaign_id"],
            state=row["state"],
            error_code=row["error_code"],
            stop_reason=result.stop_reason if result else row["error_code"],
            tool_attempts=row["tool_attempts"],
            model_requests=json.loads(row["budget_json"]).get("request_count", 0),
            result=result,
            review_result_path=receipt.get("result_path") if receipt else None,
            expires_at=row["expires"],
        )

    def verified_result(self, actor, row):
        """One authoritative replay per read; never cache a validated profile result."""
        result = None
        replayed = None
        if row["state"] == "completed":
            try:
                if (
                    row["result_json"] is None
                    or digest(json.loads(row["result_json"])) != row["result_sha"]
                ):
                    blocked("DATA_INTEGRITY_ERROR", 503)
                persisted_proof = json.loads(row["proof_json"])
                profile_expected = self.store.profile_result_expected(row, persisted_proof)
                result = parse_result(row["result_json"], profile_expected=profile_expected)
                if (
                    persisted_proof.get("rule_version") != VERSION
                    or persisted_proof["released"] != json.loads(row["disclosure_json"])
                    or {f.fact_id for f in result.facts} != set(persisted_proof["facts"])
                    or any(f.rule_version != VERSION for f in result.facts)
                ):
                    blocked("AI_PROOF_HISTORY_CONFLICT", 503)
                if isinstance(result, ProfileInvestigationResult):
                    replayed = replay_profiles(self, row, actor, result, proof)
            except (AgentError, ValueError, KeyError, TypeError):
                blocked("AI_PROFILE_PROOF_CONFLICT", 503)
        return result, replayed

    def replay(self, actor: Principal, row: dict) -> tuple[DatabaseSource, ResearchExecutor]:
        """Reconstruct issued identities only after recomputing ALL queries/disclosures.

        These are not imported historical Fact IDs: the persisted run and its entire
        ordered proof are replayed against the same current authorized DB snapshot.
        This never calls a planner, refunds a tool, or publishes a new model aggregate.
        """
        if row["state"] != "completed" or row["proof_json"] is None:
            blocked("AI_RESULT_NOT_READY")
        self.row(actor, row["batch_id"], row["task_id"], row["snapshot_id"])
        original = json.loads(row["proof_json"])
        persisted, replayed = self.verified_result(actor, row)
        if isinstance(persisted, ProfileInvestigationResult):
            return replayed
        if (
            original.get("rule_version") != VERSION
            or any(f.rule_version != VERSION for f in persisted.facts)
            or {f.fact_id for f in persisted.facts} != set(original["facts"])
        ):
            blocked("AI_RULE_OR_FACT_CONFLICT", 503)
        source = self.source(row, actor)
        executor = ResearchExecutor(source, original["executor_run_id"], Limits())
        for query in original["queries"]:
            try:
                executor.execute(query["tool"], executor.parse(query["tool"], query["arguments"]))
            except AgentError:
                if query["status"] != "rejected":
                    blocked("DATA_INTEGRITY_ERROR", 503)
        if proof(executor) != original:
            blocked("DATA_INTEGRITY_ERROR", 503)
        if original["released"] != json.loads(row["disclosure_json"]):
            blocked("AI_DISCLOSURE_HISTORY_CONFLICT", 503)
        for fact in executor.facts.values():
            executor._verify(fact)
            self.provider.replay_witnesses(
                source, [r for s in fact.item_sources for r in s.references]
            )
        return source, executor

    def records(
        self, actor: Principal, batch_id: str, task_id: str, body: SourceRequest
    ) -> SourceResult:
        row = self.row(actor, batch_id, task_id, body.snapshot_id)
        source, executor = self.replay(actor, row)
        persisted_proof = json.loads(row["proof_json"])
        original = parse_result(
            row["result_json"],
            profile_expected=self.store.profile_result_expected(row, persisted_proof),
        )
        host = InvestigationHost(self.provider, self.directory)
        host.runs[task_id] = (actor.teacher_id, source, executor, original)
        return host.records(actor, batch_id, task_id, body)
