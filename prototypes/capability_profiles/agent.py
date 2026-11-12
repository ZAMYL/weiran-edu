"""A bounded candidate-goal adapter for the EXISTING Research session and planner."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.discovery.session import _run_session

from prototypes.adaptive_assessment.agent import Decision, wire
from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.demo import MOCK_DATE, mock_authorization
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.research_agent_v2.completeness import query_key
from prototypes.research_agent_v2.demo import mock_transport
from prototypes.research_agent_v2.goals import Goal
from prototypes.research_agent_v2.runtime import (
    ResearchExecutor,
    ResearchPlanner,
    fact_record,
)

from .disclosure import check_profile_disclosure
from .engine import ProfileBook, build_profiles, query, source_sets, teacher_view
from .explanations import description_context


class ProfileExecutor(ResearchExecutor):
    def _verify(self, fact):
        started = perf_counter()
        try:
            return super()._verify(fact)
        finally:
            self.fact_replay_seconds = (
                getattr(self, "fact_replay_seconds", 0.0) + perf_counter() - started
            )

    def _gate(self, sources, staged):
        return check_profile_disclosure(
            [s.participants for s in sources],
            released=self.released,
            staged=staged,
            minimum_cell=self.limits.minimum_cell,
        )


def available_candidates(book, executor, *, verify=True):
    if book.minimum_cell != executor.limits.minimum_cell:
        raise AgentError("profile_disclosure_policy_mismatch")
    admitted, staged = [], []
    for candidate in book.candidates:
        if verify:
            book.verify(candidate.profile_id)
        try:
            checked = check_profile_disclosure(
                source_sets(candidate.discovery),
                released=executor.released,
                staged=staged,
                minimum_cell=book.minimum_cell,
            )
        except AgentError:
            continue
        staged.extend(checked)
        admitted.append(candidate)
    return admitted


class ProfilePlanner(ResearchPlanner):
    def __init__(self, executor, budget, goal, *, book: ProfileBook, **kwargs):
        if book.source.fingerprint != executor.source.fingerprint:
            raise AgentError("profile_scope_mismatch")
        self.book = book
        super().__init__(executor, budget, goal, **kwargs)
        self.schemas = {
            k: v
            for k, v in self.schemas.items()
            if k in ("compare_student_groups", "inspect_discovery_support", "finish")
        }
        self.history[0]["content"] += (
            " Choose profile candidate investigations, not quality diagnosis. Candidate directions "
            "are proposed server queries, NOT issued facts. Only actual tool facts may be selected."
        )

    def check_decision(self, decision):
        super().check_decision(decision)
        if decision.name == "compare_student_groups" and query_key(
            decision.name, decision.arguments
        ) not in {
            query_key("compare_student_groups", query(c.discovery))
            for c in available_candidates(self.book, self._executor, verify=False)
        }:
            raise AgentError("profile_query_outside_candidates")

    async def _post(self, payload: dict) -> dict:
        view = json.loads(payload["messages"][-1]["content"])
        attempted = {query_key(a["tool"], a["arguments"]) for a in self._executor.attempt_ledger}
        directions = []
        for c in available_candidates(self.book, self._executor):
            args = query(c.discovery)
            directions.append(
                {
                    "tool": "compare_student_groups",
                    "arguments": args,
                    "state": "attempted"
                    if query_key("compare_student_groups", args) in attempted
                    else "not_investigated",
                }
            )
        # No raw candidate numbers, membership, witness IDs, locators or free-text labels.
        view["profile_investigation"] = {
            "directions": directions,
            "directions_are_not_facts": True,
            "not_exhaustive": True,
            "comment_evidence": "unavailable_not_authorized",
        }
        payload["messages"][-1]["content"] = json.dumps(view, ensure_ascii=False)
        return await super()._post(payload)


class ProfileMockPolicy:
    """An explicit offline selector over actual available queries, never Qwen evidence."""

    def choose(self, view: dict) -> dict:
        pending = [
            d
            for d in view["profile_investigation"]["directions"]
            if d["state"] == "not_investigated"
        ]
        qualified = [f["fact_id"] for f in view["facts"] if f["qualified"]]
        if pending and len(qualified) < 3 and view["remaining_tool_attempts"]:
            return {"name": pending[0]["tool"], "arguments": pending[0]["arguments"]}
        return {
            "name": "finish",
            "arguments": {
                "selected_fact_ids": qualified[:3],
                "reason": "completed" if qualified else "no_valid_findings",
            },
        }


def project_selected(
    book: ProfileBook,
    result: dict,
    executor,
    *,
    staged=None,
    selection_origin="http_mock_offline_mechanism",
) -> list[dict]:
    if book.source.fingerprint != executor.source.fingerprint:
        raise AgentError("profile_scope_mismatch")
    if book.minimum_cell != executor.limits.minimum_cell:
        raise AgentError("profile_disclosure_policy_mismatch")
    if result["status"] != "completed" and result["stop_reason"] not in {
        "tool_budget_exhausted",
        "model_budget_exhausted",
    }:
        return []  # Never disguise a failed run as successfully chosen profiles.
    profiles = []
    descriptions = []
    staged = [] if staged is None else staged
    for card in result["findings"]:
        fact = executor.facts.get(card["fact_id"])
        if fact is None:
            raise AgentError("unknown_fact_id")
        executor._verify(fact)
        if fact.tool != "compare_student_groups" or not fact.view["qualified"]:
            continue
        candidate = next(
            (
                c
                for c in book.candidates
                if query_key(fact.tool, fact.query["arguments"])
                == query_key(fact.tool, query(c.discovery))
            ),
            None,
        )
        if candidate is None:
            raise AgentError("profile_fact_not_from_candidate")
        original = book.verify(candidate.profile_id).discovery
        p = original.payload
        outcome = next(d for d in p.dimensions if d.dimension == p.outcome_dimension)
        values = fact.calculation.values
        if (
            values["target_count"],
            values["remainder_count"],
            values["denominator"],
            values["target_valid"],
            values["remainder_valid"],
            values["target_mean"],
            values["remainder_mean"],
            values["difference"],
        ) != (
            len(p.selection.matches),
            len(p.remainder),
            p.selection.scope.student_count,
            outcome.target.valid_count,
            outcome.remainder.valid_count,
            outcome.target.mean,
            outcome.remainder.mean,
            outcome.difference,
        ):
            raise AgentError("profile_fact_values_mismatch")
        # Mean agreement alone is insufficient: compare the actual individual score witnesses.
        for key, stats in (
            ("target.valid", outcome.target),
            ("remainder.valid", outcome.remainder),
        ):
            actual = {w.reference_id for w in executor.resolve(fact.fact_id, key)}
            expected = {
                e.location
                for r in stats.valid_records
                for e in r.evidence_refs
                if e.dimension == p.outcome_dimension
            }
            if actual != expected:
                raise AgentError("profile_fact_sources_mismatch")
        view = teacher_view(
            book,
            candidate.profile_id,
            released=executor.released,
            staged=staged,
            previous=tuple(descriptions),
        )
        descriptions.append(description_context(p, view))
        view["verified_fact_ids"] = [fact.fact_id]
        view["selection_origin"] = selection_origin
        profiles.append(view)
    return profiles


async def run_profiles(
    recipe: str,
    state: Path,
    *,
    transport: httpx.MockTransport | None = None,
    limits: Limits | None = None,
) -> tuple:
    total_started = perf_counter()
    source = admit(recipe)
    limits = limits or Limits(timeout_seconds=30)
    book = build_profiles(source, minimum_cell=limits.minimum_cell)
    search_seconds = perf_counter() - total_started
    budget = CampaignBudget(
        mock_authorization(recipe, campaign_id="profiles-" + recipe),
        state,
        origin="http_mock",
        today=MOCK_DATE,
    )
    try:
        return await run_profile_session(
            source,
            book,
            budget,
            transport=transport,
            limits=limits,
            search_seconds=search_seconds,
            total_started=total_started,
        )
    finally:
        budget.close()


async def run_profile_session(
    source,
    book: ProfileBook,
    budget,
    *,
    transport=None,
    limits=None,
    search_seconds=0.0,
    total_started=None,
    retain_partial=True,
    staged=None,
    reviewed_execution=None,
) -> tuple:
    """Existing profile session with caller-owned budget; never allocates a campaign."""
    if reviewed_execution is not None:
        from prototypes.qwen_execution.runtime import ReviewedExecution
        from prototypes.qwen_execution.task_contract import DatabaseExecution

        if type(reviewed_execution) not in (ReviewedExecution, DatabaseExecution):
            raise AgentError("reviewed_execution_required")
        reviewed_execution.check(source, budget, transport)
    elif budget.origin != "http_mock" or (
        transport is not None and type(transport) is not httpx.MockTransport
    ):
        raise AgentError("offline_profile_session_required")
    if budget.snapshot()["claimed"]:
        raise AgentError("campaign_run_already_claimed")
    if book.source.fingerprint != source.fingerprint:
        raise AgentError("profile_scope_mismatch")
    limits = limits or Limits(timeout_seconds=30)
    total_started = perf_counter() if total_started is None else total_started
    planner = None
    started = perf_counter()
    try:
        if limits.tool_attempts <= 1:
            raise AgentError("seed_budget_exhausted")
        executor = ProfileExecutor(source, "profiles-" + uuid4().hex[:12], limits)
        executor.durable_audit = getattr(budget, "audit_tool", None)
        seed = executor.execute(
            "get_dimension_statistics", executor.parse("get_dimension_statistics", {})
        )
        planner_type = ProfilePlanner if reviewed_execution is None else reviewed_execution.planner
        planner = planner_type(
            executor,
            budget,
            Goal(kind="priority", items=()),
            book=book,
            transport=(
                transport
                if reviewed_execution is not None
                else transport
                if transport is not None
                else mock_transport(ProfileMockPolicy())
            ),
            allow_network=reviewed_execution is not None and transport is None,
            **({"settings": reviewed_execution.settings} if reviewed_execution is not None else {}),
        )
        run = await _run_session(
            executor,
            planner,
            limits=replace(limits, tool_attempts=limits.tool_attempts - 1),
            decision_type=Decision,
            argument_parser=executor.parse,
            analysis_context=executor.kernel.catalog(limits.minimum_cell),
        )
        result = wire(
            {
                "status": run.status,
                "stop_reason": run.reason,
                "findings": run.cards,
                "model_selected_fact_ids": run.selected_ids,
                "verified_observations": [
                    fact_record(executor, f) for f in executor.facts.values()
                ],
                "seed_ledger": [
                    {
                        "tool": "get_dimension_statistics",
                        "fact_ids": [f.fact_id for f in seed],
                    }
                ],
                "tool_ledger": [asdict(t) for t in run.tools],
                "attempt_ledger": executor.attempt_ledger,
                "trace": [asdict(t) for t in run.trace],
                "native_requests": planner.payloads,
                "model_choices": planner.choices,
                "reflections": planner.reflections,
                "model_requests": run.model_requests,
                "tool_attempts": 1 + run.tool_attempts,
                "verification_replay_executions": executor.replay_executions,
                "budget": budget.snapshot(),
                "elapsed_seconds": perf_counter() - started,
                "actual_tokens": None,
                "actual_cost_cny": None,
                "real_qwen": "not_run",
                "origin": "http_mock_offline_mechanism",
                "production_enabled": False,
            }
        )
        investigated = {
            query_key(f.tool, f.query["arguments"])
            for f in executor.facts.values()
            if f.tool == "compare_student_groups"
        }
        result["uninvestigated_candidates"] = sum(
            query_key("compare_student_groups", query(c.discovery)) not in investigated
            for c in book.candidates
        )
        remaining = result["uninvestigated_candidates"]
        if result["status"] == "completed" and remaining and len(run.cards) < 3:
            if result["tool_attempts"] >= limits.tool_attempts:
                result.update(status="failed", stop_reason="tool_budget_exhausted")
            elif result["model_requests"] >= limits.model_requests:
                result.update(status="failed", stop_reason="model_budget_exhausted")
            else:
                result["stop_reason"] = "stopped_with_uninvestigated_candidates"
        # Budget failure does not revoke already verified, same-session observations.
        # Retention is a server action, recorded separately from model selection.
        result["retained_fact_ids"] = []
        if result["stop_reason"] in {"tool_budget_exhausted", "model_budget_exhausted"}:
            retained = [
                f.fact_id
                for f in executor.facts.values()
                if f.tool == "compare_student_groups" and f.view["qualified"]
            ][:3]
            result["findings"] = wire(
                executor.finish(
                    executor.parse(
                        "finish",
                        {
                            "selected_fact_ids": retained,
                            "reason": "completed" if retained else "no_valid_findings",
                        },
                    )
                )
            )
            result["retained_fact_ids"] = retained
        result["profiles"] = (
            project_selected(
                book,
                result,
                executor,
                staged=staged,
                selection_origin=(
                    "http_mock_offline_mechanism" if reviewed_execution is None else budget.origin
                ),
            )
            if result["status"] == "completed" or retain_partial
            else []
        )
        result["profile_search"] = book.audit
        result["candidate_search_seconds"] = search_seconds
        result["profile_stop"] = (
            result["stop_reason"]
            if result["status"] != "completed"
            else "no_eligible_profiles"
            if not book.candidates
            else "bounded_profiles_selected"
            if len(result["profiles"]) == 3 and remaining
            else "stopped_with_uninvestigated_candidates"
            if remaining
            else "investigated_candidates_complete"
        )
        result["investigation_status"] = (
            "incomplete"
            if result["status"] != "completed" or remaining
            else "bounded_complete_not_exhaustive"
        )
        if result["profile_stop"] == "bounded_profiles_selected":
            result["investigation_status"] = "display_limited_not_exhaustive"
            result["stop_reason"] = "bounded_profiles_selected"
        result["verification_replay_executions"] = executor.replay_executions
        result["trace"].append(
            {
                "sequence": len(result["trace"]) + 1,
                "round": run.model_requests,
                "phase": "profile_finalization",
                "status": result["status"],
                "error": result["stop_reason"] if result["status"] != "completed" else None,
            }
        )
        result["elapsed_total_seconds"] = perf_counter() - total_started
        result["mock_seconds"] = sum(m.elapsed_ms for m in planner.metrics) / 1000
        if reviewed_execution is not None:
            entries = budget.snapshot()["entries"]
            usage_known = all(entry["actual_usage"] is not None for entry in entries)
            result.update(
                origin=budget.origin,
                real_qwen=(
                    "responses_received"
                    if transport is None
                    and planner.metrics
                    and all(m.status == "response_received" for m in planner.metrics)
                    else "request_failed"
                    if transport is None and planner.metrics
                    else "not_run"
                ),
                actual_tokens=(
                    sum(entry["actual_usage"]["total_tokens"] for entry in entries)
                    if usage_known
                    else None
                ),
                actual_cost_cny=(
                    budget.snapshot()["charged_cny_upper_bound"] if usage_known else None
                ),
            )
        result["tool_compute_seconds"] = executor.compute_seconds
        result["fact_replay_seconds"] = getattr(executor, "fact_replay_seconds", 0.0)
        return result, book, executor
    finally:
        if planner is not None:
            await planner.aclose()
