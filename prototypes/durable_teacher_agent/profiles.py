"""Trusted profile execution/replay inside the existing durable task and budget."""

import json
from dataclasses import asdict
from time import perf_counter

import httpx
from app.ai.edu.discovery.models import AgentError

from prototypes.adaptive_assessment.live.budget import OfflineSnapshotPermit
from prototypes.capability_profiles.agent import (
    ProfileExecutor,
    ProfileMockPolicy,
    project_selected,
    run_profile_session,
)
from prototypes.capability_profiles.database import DatabaseProfileSource
from prototypes.capability_profiles.engine import VERSION, build_profiles, query
from prototypes.capability_profiles.explanations import VERSION as EXPLANATION_VERSION
from prototypes.research_agent_v2.completeness import query_key
from prototypes.research_agent_v2.demo import mock_transport
from prototypes.research_agent_v2.goals import Goal
from prototypes.teacher_agent_bridge.api import filter_conditions, metrics
from prototypes.teacher_agent_bridge.models import InvestigationResult, SourceRequest
from prototypes.teacher_agent_bridge.provider import ALIASES, DatabaseSource, digest

from .models import ProfileInvestigationResult, ProfileProjection
from .store import blocked


def parse_result(raw: str, *, profile_expected: bool) -> InvestigationResult:
    payload = json.loads(raw)
    if not isinstance(payload, dict) or (("profile_projection" in payload) is not profile_expected):
        blocked("AI_PROFILE_PROOF_CONFLICT", 503)
    model = ProfileInvestigationResult if profile_expected else InvestigationResult
    return model.model_validate_json(raw)


def combined_disclosure(executor, staged) -> list[list[str]]:
    groups = list(executor.released)
    for group in staged:
        if group not in groups:
            groups.append(group)
    return [sorted(group) for group in groups]


def uncovered(book, executor) -> list[str]:
    investigated = {
        query_key(f.tool, f.query["arguments"])
        for f in executor.facts.values()
        if f.tool == "compare_student_groups"
    }
    return [
        c.profile_id
        for c in book.candidates
        if query_key("compare_student_groups", query(c.discovery)) not in investigated
    ]


def projection(source, raw, book, executor) -> ProfileProjection:
    profiles = []
    for original in raw["profiles"]:
        view = json.loads(json.dumps(original))
        # The engine's internal discovery snapshot is not an assessment API snapshot.
        view["analysis_snapshot_id"] = view["snapshot_id"]
        view["snapshot_id"] = source.envelope.bound_snapshot
        view["source_manifest_sha256"] = source.fingerprint
        view["exploration_query"]["snapshot_id"] = source.envelope.bound_snapshot
        view.pop("source_queries", None)  # Offline resolvers are NOT implemented HTTP routes.
        view["dimension_evidence_http"] = "not_implemented"
        profiles.append(view)
    return ProfileProjection(
        assessment_snapshot_id=source.envelope.bound_snapshot,
        source_manifest_sha256=source.fingerprint,
        profile_rule_version=VERSION,
        explanation_rule_version=EXPLANATION_VERSION,
        minimum_cell=book.minimum_cell,
        profiles=tuple(profiles),
        stop_reason=raw["profile_stop"],
        investigation_status=raw["investigation_status"],
        uninvestigated_candidates=len(uncovered(book, executor)),
        search_audit=book.audit.copy(),
        timings_seconds={
            "candidate_search": raw.get("candidate_search_seconds", 0.0),
            "model_mock": raw.get("mock_seconds", 0.0),
            "tool_execution": executor.compute_seconds,
            "fact_source_replay": getattr(executor, "fact_replay_seconds", 0.0),
            "profile_source_replay": book.audit.get("projection_source_replay_seconds", 0.0),
            "total": raw.get("elapsed_total_seconds", 0.0),
        },
        verification_replays=executor.replay_executions,
    )


def stable_projection(value: ProfileProjection) -> dict:
    return value.model_dump(
        mode="json", exclude={"search_audit", "timings_seconds", "verification_replays"}
    )


async def investigate_profiles(source, question, budget, limits):
    with source.validation_scope():
        return await _investigate_profiles(source, question, budget, limits)


async def _investigate_profiles(source, question, budget, limits):
    transport = mock_transport(ProfileMockPolicy())
    if (
        type(source) is not DatabaseSource
        or type(transport) is not httpx.MockTransport
        or budget.origin != "http_mock"
        or type(budget.authorization) is not OfflineSnapshotPermit
        or budget.authorization.source is not source
    ):
        raise AgentError("offline_database_adapter_required")
    started = perf_counter()
    contract = DatabaseProfileSource(source)
    contract.validate()
    book = build_profiles(contract, minimum_cell=limits.minimum_cell)
    search_seconds = perf_counter() - started
    staged = []
    raw, _, executor = await run_profile_session(
        source,
        book,
        budget,
        transport=transport,
        limits=limits,
        search_seconds=search_seconds,
        total_started=started,
        retain_partial=False,
        staged=staged,
    )
    raw["goal"] = Goal(kind="priority", items=()).view()
    raw["missing_evidence"] = []
    raw["trace"] = [
        {
            "tool": event.get("tool"),
            "round": event["round"],
            "phase": event["phase"],
            "status": event["status"],
            "fact_ids": event.get("fact_ids", []),
        }
        for event in raw["trace"]
    ]
    raw["investigation_coverage"] = {"uncovered_direction_ids": uncovered(book, executor)}
    if raw["status"] == "completed":
        # Keep #72's empty-result contract; the optional projection adds profile detail.
        raw["stop_reason"] = raw["profile_stop"] if book.candidates else "no_supported_findings"
        projected = projection(source, raw, book, executor)
    else:
        projected = None  # #72 has no safe partial-result state: fail closed, never publish.
    source.validate()
    return raw, executor, projected, staged


def extend_proof(tool_proof, result: ProfileInvestigationResult, executor, staged, limits):
    extended = dict(tool_proof)
    extended["tool_proof"] = tool_proof
    extended["released"] = combined_disclosure(executor, staged)
    extended["profile"] = {
        "version": VERSION,
        "explanation_version": EXPLANATION_VERSION,
        "limits": asdict(limits),
        "selected_fact_ids": list(result.selected_fact_ids),
        "projection_sha": digest(stable_projection(result.profile_projection)),
    }
    return extended


def replay_profiles(
    service,
    row,
    actor,
    result: ProfileInvestigationResult,
    make_tool_proof,
    *,
    source=None,
):
    source = source or service.source(row, actor)
    with source.validation_scope():
        return _replay_profiles(service, row, actor, result, make_tool_proof, source)


def _replay_profiles(service, row, actor, result, make_tool_proof, source):
    original = json.loads(row["proof_json"])
    if (
        original["profile"]["limits"] != asdict(service.profile_limits)
        or original["profile"]["version"] != VERSION
        or original["profile"]["explanation_version"] != EXPLANATION_VERSION
    ):
        blocked("AI_PROFILE_RULE_OR_POLICY_CONFLICT", 503)
    if (
        result.run_id != row["task_id"]
        or result.batch_id != row["batch_id"]
        or result.snapshot_id != row["snapshot_id"]
        or result.source_manifest_sha256 != source.fingerprint
        or result.batch_record_count != len(source.envelope.data.participants)
        or result.status != "completed"
        or result.controlled_goal != "priority"
        or result.goal_items
        or result.budget.model_requests != json.loads(row["budget_json"])["request_count"]
        or result.budget.tool_attempts != row["tool_attempts"]
    ):
        blocked("AI_PROFILE_SCOPE_OR_BUDGET_CONFLICT", 503)
    contract = DatabaseProfileSource(source)
    book = build_profiles(contract, minimum_cell=service.profile_limits.minimum_cell)
    tool_proof = original["tool_proof"]
    executor = ProfileExecutor(source, tool_proof["executor_run_id"], service.profile_limits)
    # No durable_audit or planner: replay does not spend/refund task budgets.
    for attempt in tool_proof["queries"]:
        try:
            executor.execute(attempt["tool"], executor.parse(attempt["tool"], attempt["arguments"]))
        except AgentError:
            if attempt["status"] != "rejected":
                blocked("DATA_INTEGRITY_ERROR", 503)
    if make_tool_proof(executor) != tool_proof:
        blocked("AI_PROFILE_PROOF_CONFLICT", 503)
    if {f.fact_id for f in result.facts} != set(executor.facts) or len(result.facts) != len(
        executor.facts
    ):
        blocked("AI_PROFILE_FACT_CONFLICT", 503)
    for published in result.facts:
        fact = executor.facts[published.fact_id]
        if (
            published.batch_id != row["batch_id"]
            or published.snapshot_id != row["snapshot_id"]
            or published.method != fact.tool
            or not published.source_verified
            or published.qualified != fact.view["qualified"]
            or published.metrics != tuple(metrics(fact.view))
            or published.conditions != filter_conditions(fact.query["arguments"])
            or len(published.source_refs) != len(fact.item_sources)
        ):
            blocked("AI_PROFILE_FACT_CONFLICT", 503)
        for link, actual in zip(published.source_refs, fact.item_sources, strict=True):
            expected_path = (
                f"/api/v1/edu/batches/{row['batch_id']}/ai-investigation-tasks/"
                f"{row['task_id']}/records/query"
            )
            if (
                link.metric_key != actual.key
                or link.dimension_code != ALIASES[actual.item_id]
                or link.participant_count != len(actual.participants)
                or link.records_path != expected_path
                or link.records_query
                != SourceRequest(
                    snapshot_id=row["snapshot_id"], fact_id=fact.fact_id, metric_key=actual.key
                )
            ):
                blocked("AI_PROFILE_WITNESS_BINDING_CONFLICT", 503)
    executor.verify_all()
    service.provider.replay_witnesses(
        source,
        [
            reference
            for fact in executor.facts.values()
            for metric_source in fact.item_sources
            for reference in metric_source.references
        ],
    )
    staged = []
    raw = {
        "status": "completed",
        "stop_reason": result.stop_reason,
        "findings": [{"fact_id": f} for f in result.selected_fact_ids],
    }
    raw["profiles"] = project_selected(book, raw, executor, staged=staged)
    remaining = len(uncovered(book, executor))
    raw["profile_stop"] = (
        "no_eligible_profiles"
        if not book.candidates
        else "bounded_profiles_selected"
        if len(raw["profiles"]) == 3 and remaining
        else "stopped_with_uninvestigated_candidates"
        if remaining
        else "investigated_candidates_complete"
    )
    raw["investigation_status"] = (
        "display_limited_not_exhaustive"
        if raw["profile_stop"] == "bounded_profiles_selected"
        else "incomplete"
        if remaining
        else "bounded_complete_not_exhaustive"
    )
    expected_stop = raw["profile_stop"] if book.candidates else "no_supported_findings"
    if result.stop_reason != expected_stop or result.uncovered_direction_count != remaining:
        blocked("AI_PROFILE_COVERAGE_CONFLICT", 503)
    current = projection(source, raw, book, executor)
    if stable_projection(current) != stable_projection(result.profile_projection):
        blocked("AI_PROFILE_PROJECTION_CONFLICT", 503)
    expected = extend_proof(tool_proof, result, executor, staged, service.profile_limits)
    if expected != original or expected["released"] != json.loads(row["disclosure_json"]):
        blocked("AI_DISCLOSURE_HISTORY_CONFLICT", 503)
    for key, value in book.audit.items():
        if (
            not key.startswith("projection_source_replay")
            and result.profile_projection.search_audit.get(key) != value
        ):
            blocked("AI_PROFILE_SEARCH_PROOF_CONFLICT", 503)
    source.validate()
    return source, executor
