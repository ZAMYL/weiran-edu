"""Reuse #70 projection and #66 executor, backed by SQLite proof/disclosure journals."""

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from threading import Lock

from app.ai.edu.discovery.models import AgentError, Limits
from app.core.errors import APIError
from app.edu.auth import Principal

from prototypes.adaptive_assessment.analysis import VERSION
from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.capability_profiles.engine import CODES, DIMS, ITEMS
from prototypes.research_agent_v2.runtime import ResearchExecutor
from prototypes.teacher_agent_bridge.api import InvestigationHost
from prototypes.teacher_agent_bridge.models import (
    InvestigationRequest,
    SourceRequest,
    SourceResult,
)
from prototypes.teacher_agent_bridge.provider import DatabaseEnvelope, DatabaseSource, digest

from .models import (
    PROFILE_SCORE_EVIDENCE_MODE,
    ProfileInvestigationResult,
    ProfileScoreEvidence,
    ProfileScoreRequest,
    ProfileScoreResult,
    TaskView,
)
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
        self._review_lock = Lock()
        self._active_reviews: set[str] = set()
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

    def _row(self, actor: Principal, batch_id: str, task_id: str, snapshot_id: str) -> dict:
        row = self.store.get(actor, task_id)
        if row["batch_id"] != batch_id or row["snapshot_id"] != snapshot_id:
            blocked("SNAPSHOT_MISMATCH")
        return row

    def row(self, actor: Principal, batch_id: str, task_id: str, snapshot_id: str) -> dict:
        row = self._row(actor, batch_id, task_id, snapshot_id)
        self.source(row, actor).validate()
        return row

    def task_row(self, actor: Principal, batch_id: str, task_id: str, snapshot_id: str) -> dict:
        """Authorization and snapshot binding only; used by cheap status-like reads."""
        return self._row(actor, batch_id, task_id, snapshot_id)

    @contextmanager
    def review_submission(self, task_id: str) -> Iterator[None]:
        """Avoid redundant in-flight work in this process. SQLite owns the save guard."""
        with self._review_lock:
            if task_id in self._active_reviews:
                blocked("AI_REVIEW_IN_PROGRESS")
            self._active_reviews.add(task_id)
        try:
            yield
        finally:
            with self._review_lock:
                self._active_reviews.remove(task_id)

    def view(self, actor: Principal, batch_id: str, task_id: str, snapshot_id: str) -> TaskView:
        row = self._row(actor, batch_id, task_id, snapshot_id)
        source = self.source(row, actor)
        result = None
        if row["state"] == "completed":
            # source() has just materialized and checked this exact snapshot, so the
            # enclosing phase skips its duplicate entry capture and still re-checks the
            # whole source on exit before anything is returned.
            with source.validation_scope(prevalidated=True):
                result, _ = self.verified_result(actor, row, source=source)
        else:
            # source() has just verified the current snapshot fingerprint.
            # Status-only replies have no Fact or Witness replay to enclose.
            self.store.authorize(task_id, actor)
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

    def verified_result(self, actor, row, *, source=None):
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
                    replayed = replay_profiles(self, row, actor, result, proof, source=source)
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
        source = self.source(row, actor)
        original = json.loads(row["proof_json"])
        with source.validation_scope():
            persisted, replayed = self.verified_result(actor, row, source=source)
            if isinstance(persisted, ProfileInvestigationResult):
                return replayed.source, replayed.executor
            if (
                original.get("rule_version") != VERSION
                or any(f.rule_version != VERSION for f in persisted.facts)
                or {f.fact_id for f in persisted.facts} != set(original["facts"])
            ):
                blocked("AI_RULE_OR_FACT_CONFLICT", 503)
            executor = ResearchExecutor(source, original["executor_run_id"], Limits())
            for query in original["queries"]:
                try:
                    executor.execute(
                        query["tool"], executor.parse(query["tool"], query["arguments"])
                    )
                except AgentError:
                    if query["status"] != "rejected":
                        blocked("DATA_INTEGRITY_ERROR", 503)
            if proof(executor) != original:
                blocked("DATA_INTEGRITY_ERROR", 503)
            if original["released"] != json.loads(row["disclosure_json"]):
                blocked("AI_DISCLOSURE_HISTORY_CONFLICT", 503)
            executor.verify_all()
            self.provider.replay_witnesses(
                source,
                [
                    reference
                    for fact in executor.facts.values()
                    for metric_source in fact.item_sources
                    for reference in metric_source.references
                ],
            )
            return source, executor

    def profile_scores(
        self, actor: Principal, batch_id: str, task_id: str, body: ProfileScoreRequest
    ) -> ProfileScoreResult:
        """Resolve one already-published profile dimension without issuing a new Fact."""

        row = self._row(actor, batch_id, task_id, body.snapshot_id)
        source = self.source(row, actor)
        with source.validation_scope():
            result, replayed = self.verified_result(actor, row, source=source)
            if not isinstance(result, ProfileInvestigationResult) or replayed is None:
                blocked("AI_PROFILE_NOT_FOUND", 404)
            if result.profile_projection.dimension_evidence_http != PROFILE_SCORE_EVIDENCE_MODE:
                blocked("AI_PROFILE_EVIDENCE_NOT_ENABLED")
            profiles = [
                profile
                for profile in result.profile_projection.profiles
                if profile.get("profile_id") == body.profile_id
            ]
            if len(profiles) != 1:
                blocked("AI_PROFILE_NOT_FOUND", 404)
            profile = profiles[0]
            dimensions = [
                dimension
                for dimension in profile.get("dimensions", [])
                if dimension.get("dimension_code") == body.dimension_code
            ]
            if len(dimensions) != 1:
                blocked("AI_PROFILE_DIMENSION_NOT_FOUND", 404)
            published = dimensions[0][body.side]
            dimension = DIMS[CODES.index(body.dimension_code)]
            item_id = ITEMS[DIMS.index(dimension)]
            candidate = replayed.book.verify(body.profile_id)
            statistics = next(
                value
                for value in candidate.discovery.payload.dimensions
                if value.dimension == dimension
            )
            actual = getattr(statistics, body.side)
            published_mean = published["mean"]
            if (
                published["valid_count"] != actual.valid_count
                or Fraction(published_mean["numerator"], published_mean["denominator"])
                != actual.mean
            ):
                blocked("AI_PROFILE_PROJECTION_CONFLICT", 503)
            profile_fact_ids = tuple(profile.get("verified_fact_ids", ()))
            facts = []
            for fact_id in profile_fact_ids:
                fact = replayed.executor.facts.get(fact_id)
                if fact is None:
                    blocked("AI_PROFILE_FACT_CONFLICT", 503)
                facts.append(fact)
            dimension_fact_ids = tuple(
                fact.fact_id for fact in facts if fact.calculation.values.get("outcome") == item_id
            )
            common = {
                "task_id": task_id,
                "batch_id": batch_id,
                "snapshot_id": body.snapshot_id,
                "profile_id": body.profile_id,
                "dimension_code": body.dimension_code,
                "side": body.side,
                "source_manifest_sha256": source.fingerprint,
                "verified_profile_fact_ids": profile_fact_ids,
                "dimension_fact_ids": dimension_fact_ids,
                "mean": {
                    "numerator": actual.mean.numerator,
                    "denominator": actual.mean.denominator,
                },
                "valid_count": actual.valid_count,
                "offset": body.offset,
                "limit": body.limit,
            }
            if published.get("distribution_state") != "available":
                return ProfileScoreResult(
                    **common,
                    detail_status="privacy_limited",
                    total_count=None,
                    has_more=False,
                    items=(),
                    limitation_code="PROFILE_SCORE_DETAIL_PRIVACY_LIMITED",
                )

            references = []
            for record in actual.valid_records:
                matches = [
                    evidence.location
                    for evidence in record.evidence_refs
                    if evidence.dimension == dimension
                ]
                if len(matches) != 1:
                    blocked("AI_PROFILE_WITNESS_BINDING_CONFLICT", 503)
                references.append(matches[0])
            witnesses = self.provider.replay_witnesses(source, references)
            validated_records = {
                record.reference.student_id: record for record in replayed.book.dataset.records
            }
            links = []
            for record, reference, witness in zip(
                actual.valid_records, references, witnesses, strict=True
            ):
                validated = validated_records.get(record.student_id)
                score = None if validated is None else validated.score(dimension).value
                if (
                    score is None
                    or validated.reference != record
                    or witness.reference_id != reference
                    or witness.participant_id != record.student_id
                    or witness.item_id != item_id
                    or witness.kind != "score"
                    or Fraction(witness.value) != score
                    or witness.source_sha256 != source.fingerprint
                ):
                    blocked("AI_PROFILE_WITNESS_BINDING_CONFLICT", 503)
                links.append(
                    ProfileScoreEvidence(
                        student_id=record.student_id,
                        evidence_id=reference,
                        dimension_code=body.dimension_code,
                        score={"numerator": score.numerator, "denominator": score.denominator},
                        evidence_path=f"/api/v1/edu/batches/{batch_id}/evidence/{reference}",
                    )
                )
            released = {frozenset(group) for group in json.loads(row["disclosure_json"]) if group}
            for bucket in actual.distribution:
                members = frozenset(
                    link.student_id
                    for link in links
                    if Fraction(link.score.numerator, link.score.denominator) == bucket.score
                )
                if members and members not in released:
                    blocked("AI_DISCLOSURE_HISTORY_CONFLICT", 503)
            source.validate()
            return ProfileScoreResult(
                **common,
                detail_status="available",
                total_count=len(links),
                has_more=body.offset + body.limit < len(links),
                items=tuple(links[body.offset : body.offset + body.limit]),
            )

    def records(
        self, actor: Principal, batch_id: str, task_id: str, body: SourceRequest
    ) -> SourceResult:
        row = self._row(actor, batch_id, task_id, body.snapshot_id)
        source, executor = self.replay(actor, row)
        persisted_proof = json.loads(row["proof_json"])
        original = parse_result(
            row["result_json"],
            profile_expected=self.store.profile_result_expected(row, persisted_proof),
        )
        host = InvestigationHost(self.provider, self.directory)
        host.runs[task_id] = (actor.teacher_id, source, executor, original)
        with source.validation_scope():
            return host.records(actor, batch_id, task_id, body)
