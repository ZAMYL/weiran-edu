"""One-use synthetic history replay, not a teacher authorization or persistence service."""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

import httpx
from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.discovery.session import _run_session
from app.analysis.edu.discovery.models import VERSION as DISCOVERY_VERSION
from app.analysis.edu.discovery.models import Policy

from evals.discovery_v1.cases import cases
from prototypes.adaptive_assessment.agent import (
    AdaptiveExecutor,
    AdaptiveFact,
    Decision,
    wire,
)
from prototypes.adaptive_assessment.analysis import VERSION
from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.demo import (
    MOCK_DATE,
    mock_authorization,
    native_response,
)
from prototypes.adaptive_assessment.live.planner import QwenAdaptivePlanner
from prototypes.adaptive_assessment.synthetic import admit

from .collector import Collected, collect, proposal_valid
from .grading import grade_native
from .handoff_probe import _signatures
from .reference import Spec, frozen_spec
from .runner import check_admitted_input, spec_for_recipe, verify_dependencies


def digest(value: object) -> str:
    return sha256(json.dumps(wire(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def spec(recipe: str) -> Spec:
    if recipe == "five":
        return frozen_spec(next(c for c in cases() if c.case_id == "group-53"))
    if recipe == "contest13":
        return spec_for_recipe(recipe)
    raise AgentError("unsupported_synthetic_history")


@dataclass(frozen=True)
class Context:
    recipe: str
    batch_id: str
    snapshot_id: str
    source_sha256: str
    scope_sha256: str
    rule_version: str
    policy_sha256: str
    minimum_cell: int = 5
    purpose: str = "offline_synthetic_handoff"


def context_for(recipe: str) -> Context:
    source = admit(recipe)
    check_admitted_input(spec(recipe), source)
    return Context(
        recipe,
        source.envelope.data.batch.batch_id,
        source.fingerprint,
        sha256(source.envelope.source_bytes).hexdigest(),
        digest(sorted(p.participant_id for p in source.envelope.data.participants)),
        VERSION,
        digest(
            {
                "adaptive_version": VERSION,
                "discovery_version": DISCOVERY_VERSION,
                "policy": asdict(Policy()),
            }
        ),
    )


def proof(fact: AdaptiveFact) -> str:
    # IDs are issuer-local. Keep semantic values and actual metric sources, never transplant IDs.
    return digest(
        {
            "view": {k: v for k, v in fact.view.items() if k != "fact_id"},
            "sources": [asdict(s) for s in fact.item_sources],
        }
    )


def disclosures(executor: AdaptiveExecutor) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(sorted(s)) for s in executor.released)


def checked_grade(recipe: str, captured: Collected) -> dict:
    result = grade_native(spec(recipe), captured)
    if (
        result["card_projection_errors"]
        or result["disclosure_errors"]
        or any(not f["data_correct"] or not f["sources_correct"] for f in result["fact_checks"])
    ):
        raise AgentError("independent_history_validation_failed")
    return result


@dataclass(frozen=True)
class History:
    context: Context
    queries: tuple[str, ...]
    proofs: tuple[str, ...]
    disclosed: tuple[tuple[str, ...], ...]
    retained_indexes: tuple[int, ...]
    original_ids: tuple[str, ...]
    model_requests: int
    tool_attempts: int


class SyntheticHistoryStore:
    """Only create() writes history, by executing the closed source itself. No JSON importer."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[History, str]] = {}
        self._used: set[str] = set()

    async def create(self, recipe: str, state: Path) -> tuple[str, Collected]:
        verify_dependencies()
        context = context_for(recipe)
        prior = await collect(recipe, state, profile="fixed")
        grade = checked_grade(recipe, prior)
        facts = tuple(prior.executor.facts.values())
        if prior.run.status != "completed" or any(t.status != "accepted" for t in prior.run.tools):
            raise AgentError("incomplete_history")
        if not 1 <= len(prior.run.selected_ids) <= 2 or len(facts) > 3:
            raise AgentError("unsupported_history_size")
        if len(_signatures(prior, grade)) != len(prior.run.selected_ids):
            raise AgentError("duplicate_or_unqualified_history")
        if any(f.tool == "inspect_discovery_support" for f in facts):
            raise AgentError("history_support_dependency_not_supported")
        history = History(
            context,
            tuple(json.dumps(f.query, sort_keys=True) for f in facts),
            tuple(proof(f) for f in facts),
            disclosures(prior.executor),
            tuple(i for i, f in enumerate(facts) if f.fact_id in prior.run.selected_ids),
            tuple(f.fact_id for f in facts),
            prior.run.model_requests,
            prior.run.tool_attempts,
        )
        reference = "history-" + uuid4().hex
        self._entries[reference] = history, digest(asdict(history))
        return reference, prior

    def admit(
        self, reference: str, context: Context
    ) -> tuple[AdaptiveExecutor, History, tuple[str, ...], list[dict[str, str]], float]:
        if type(reference) is not str or reference not in self._entries:
            raise AgentError("unknown_history_reference")
        if reference in self._used:
            raise AgentError("history_already_consumed")
        history, seal = self._entries[reference]
        if digest(asdict(history)) != seal:
            raise AgentError("history_integrity_mismatch")
        if type(context) is not Context or context != history.context:
            raise AgentError("history_context_mismatch")
        if context_for(context.recipe) != context:
            raise AgentError("history_context_mismatch")
        if history.model_requests >= 6 or history.tool_attempts >= 5:
            raise AgentError("history_budget_exhausted")
        # One attempt per handle, including failed replay; failures cannot restore the allowance.
        self._used.add(reference)
        started = perf_counter()
        executor = AdaptiveExecutor(
            admit(context.recipe),
            "run-" + uuid4().hex[:16],
            Limits(
                model_requests=6 - history.model_requests,
                tool_attempts=5 - history.tool_attempts,
                timeout_seconds=30,
            ),
        )
        if len(history.queries) != len(history.proofs) or len(history.queries) > 3:
            raise AgentError("history_integrity_mismatch")
        for query, expected in zip(history.queries, history.proofs, strict=True):
            query = json.loads(query)
            (fact,) = executor.execute(
                query["tool"], executor.parse(query["tool"], query["arguments"])
            )
            executor._verify(fact)
            if proof(fact) != expected:
                raise AgentError("history_replay_mismatch")
        if disclosures(executor) != history.disclosed:
            raise AgentError("history_disclosure_mismatch")
        facts = tuple(executor.facts.values())
        retained = tuple(facts[i].fact_id for i in history.retained_indexes)
        # Existing finish() rechecks qualification, source replay and duplicate selection.
        executor.finish(
            executor.parse("finish", {"selected_fact_ids": list(retained), "reason": "completed"})
        )
        lineage = [
            {
                "prior_ref": digest([asdict(context), history.queries[i]]),
                "current_fact_id": facts[i].fact_id,
            }
            for i in history.retained_indexes
        ]
        return executor, history, retained, lineage, perf_counter() - started


class Goal(StrEnum):
    OBSERVATIONS = "verify_relation_and_group_observations"
    MISSINGNESS = "investigate_observed_missingness"
    CAUSE = "request_explanation_of_relation"


class HandoffMockPlanner(QwenAdaptivePlanner):
    """Eval-only typed goal seam over #58. A concrete MockTransport is mandatory."""

    def __init__(
        self,
        executor: AdaptiveExecutor,
        budget: CampaignBudget,
        goal: Goal,
        transport: httpx.MockTransport,
    ) -> None:
        if type(goal) is not Goal or type(transport) is not httpx.MockTransport:
            raise AgentError("offline_handoff_only")
        self.goal = goal
        super().__init__(executor, budget, transport=transport)

    async def _post(self, payload: dict) -> dict:
        # The base rebuilt the aggregate view. Add only a server-owned enum, no question/JSON.
        payload = json.loads(json.dumps(payload))
        message = payload["messages"][-1]
        view = json.loads(message["content"])
        view["task"] = {
            "goal": self.goal.value,
            "rubric_tool_available": False,
            "task_evidence_tool_available": False,
        }
        message["content"] = json.dumps(view, ensure_ascii=False)
        return await super()._post(payload)


def mock_choice(view: dict) -> dict:
    """Declared deterministic responder, data-dependent; NOT model reasoning evidence."""
    qualified = [f for f in view["facts"] if f["qualified"]]
    if view["task"]["goal"] == Goal.MISSINGNESS:
        quality = [f for f in qualified if f.get("type") == "item_quality_review"]
        if not quality:
            summaries = [f for f in view["facts"] if "items" in f]
            issues = [
                (
                    sum(v for k, v in item.get("states", {}).items() if k != "valid"),
                    item["item_id"],
                    summary["fact_id"],
                )
                for summary in summaries
                for item in summary["items"]
            ]
            if issues and max(issues)[0]:
                _, item, basis = max(issues)
                return {
                    "name": "inspect_item_quality",
                    "arguments": {"item_id": item},
                    "basis_fact_ids": [basis],
                }
        # Deliberately selects only new quality evidence: the server must still retain priors.
        qualified = quality
    return {
        "name": "finish",
        "arguments": {
            "selected_fact_ids": [f["fact_id"] for f in qualified],
            "reason": "completed" if qualified else "no_valid_findings",
        },
    }


async def continue_history(
    store: SyntheticHistoryStore,
    reference: str,
    context: Context,
    goal: Goal,
    state: Path,
    *,
    fault=None,
) -> dict:
    if type(goal) is not Goal:
        raise AgentError("invalid_handoff_goal")
    executor, history, retained, lineage, validation_seconds = store.admit(reference, context)
    prior_ids = set(executor.facts)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        view = json.loads(payload["messages"][-1]["content"])
        chosen = mock_choice(view)
        if fault:
            chosen = fault(chosen, view)
        requests.append(
            {
                "messages": payload["messages"],
                "tools": payload["tools"],
                "proposal": chosen,
            }
        )
        return httpx.Response(200, json=native_response(chosen, len(requests) - 1))

    authorization = mock_authorization(
        context.recipe,
        campaign_id="handoff-" + uuid4().hex,
        max_requests=executor.limits.model_requests,
    )
    budget = CampaignBudget(authorization, state, origin="http_mock", today=MOCK_DATE)
    planner = HandoffMockPlanner(executor, budget, goal, httpx.MockTransport(handler))
    started = perf_counter()
    try:
        run = await _run_session(
            executor,
            planner,
            limits=executor.limits,
            decision_type=Decision,
            argument_parser=executor.parse,
            analysis_context=executor.kernel.catalog(),
        )
        # Model selection cannot delete admitted priors. Failed investigation is still explicit.
        selected = tuple(dict.fromkeys((*retained, *run.selected_ids)))
        cards = executor.finish(
            executor.parse("finish", {"selected_fact_ids": list(selected), "reason": "completed"})
        )
        final = replace(run, cards=cards, selected_ids=selected)
        audit = {
            "model_proposals": [
                {
                    "decision": r["proposal"],
                    "legal_domain_request": proposal_valid(
                        r["proposal"], json.loads(r["messages"][-1]["content"])
                    ),
                }
                for r in requests
            ],
            "budget": budget.snapshot(),
            "model_requests": len(requests),
            "elapsed_seconds": perf_counter() - started,
            "operation_counts": {},
        }
        grade = checked_grade(context.recipe, Collected(final, executor, audit))
        types = {executor.facts[i].view.get("type") for i in selected}
        gaps = ["rubric_criteria", "original_task_evidence"] if goal == Goal.CAUSE else []
        satisfied = (
            {"item_relation", "item_group_difference"} <= types
            if goal == Goal.OBSERVATIONS
            else "item_quality_review" in types
            if goal == Goal.MISSINGNESS
            else "item_relation" in types
        )
        stop = (
            "investigation_failed"
            if run.status != "completed"
            else "goal_unmet"
            if not satisfied
            else "needs_external_evidence"
            if gaps
            else "satisfied_by_existing"
            if set(selected) <= prior_ids
            else "new_evidence_obtained"
        )
        return wire(
            {
                "origin": "offline_mechanism",
                "real_qwen": "not_run",
                "goal": goal.value,
                "status": run.status,
                "stop_reason": stop,
                "native_stop_reason": run.reason,
                "existing_findings": lineage,
                "admission_ledger": [
                    {
                        "query": f.query,
                        "fresh_fact_id": f.fact_id,
                        "source_and_values_verified": True,
                    }
                    for f in tuple(executor.facts.values())[: len(history.queries)]
                ],
                "new_issued_facts": [f.view for f in run.facts if f.fact_id not in prior_ids],
                "missing_evidence": gaps,
                "cards": cards,
                "grade": grade,
                "counts": {
                    "prior": len(retained),
                    "retained": len(set(retained) & set(selected)),
                    "new_findings": len(set(selected) - set(retained)),
                    "omitted": len(set(retained) - set(selected)),
                    "duplicate_tool_attempts": sum(
                        t.error in {"repeated_tool", "duplicate_discovery"} for t in run.tools
                    ),
                    "history_model_requests": history.model_requests,
                    "history_origin": "offline_deterministic_policy",
                    "new_http_mock_requests": len(requests),
                    "new_model_requests": run.model_requests,
                    "total_model_requests": history.model_requests + run.model_requests,
                    "history_tool_attempts": history.tool_attempts,
                    "new_tool_attempts": run.tool_attempts,
                    "total_tool_attempts": history.tool_attempts + run.tool_attempts,
                    "admission_replay_executions": len(history.queries),
                },
                "admission_seconds": validation_seconds,
                "mock_budget": budget.snapshot(),
                "actual_tokens": None,
                "actual_cost_cny": None,
                "trace": [asdict(t) for t in run.trace],
                "tools": [asdict(t) for t in run.tools],
                "http_transcript": requests,
                "model_selected": run.selected_ids,
                "replay_old_fact_ids_copied": bool(set(history.original_ids) & set(executor.facts)),
                "production_authorization": "not_implemented",
                "cross_session_disclosure_service": "not_implemented",
            }
        )
    finally:
        await planner.aclose()
        budget.close()


async def build_record(state: Path) -> dict:
    verify_dependencies()
    store = SyntheticHistoryStore()
    output = {}
    for label, recipe, goal in (
        ("A", "five", Goal.OBSERVATIONS),
        ("B", "contest13", Goal.MISSINGNESS),
        ("C", "five", Goal.CAUSE),
    ):
        ref, prior = await store.create(recipe, state)
        current = await continue_history(store, ref, context_for(recipe), goal, state)
        current["prior_grade"] = grade_native(spec(recipe), prior)
        output[label] = current
    return {
        "version": "synthetic-trusted-handoff-0.1",
        "cases": output,
        "real_qwen": "not_run",
        "paid_api_calls": 0,
        "scope": "process-local registered synthetic sources only; no teacher authorization",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.suffix != ".json":
        parser.error("new_JSON_output_required_preserve_failures")
    with TemporaryDirectory(prefix="edu-trusted-handoff-") as state:
        result = asyncio.run(build_record(Path(state)))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {k: {"stop": v["stop_reason"], **v["counts"]} for k, v in result["cases"].items()}
        )
    )
    expected = {
        "A": "satisfied_by_existing",
        "B": "new_evidence_obtained",
        "C": "needs_external_evidence",
    }
    return int(
        any(
            c["status"] != "completed"
            or c["counts"]["omitted"]
            or c["stop_reason"] != expected[label]
            for label, c in result["cases"].items()
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
