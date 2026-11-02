"""Actual #58 HTTP Mock collection. No environment credentials or paid execution entry."""

import cProfile
import json
import re
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from app.ai.edu.discovery.models import AgentRun, Limits
from app.ai.edu.discovery.session import _run_session

from prototypes.adaptive_assessment.agent import SCHEMAS, AdaptiveExecutor, Decision, wire
from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.demo import MOCK_DATE, mock_authorization, native_response
from prototypes.adaptive_assessment.live.planner import QwenAdaptivePlanner
from prototypes.adaptive_assessment.planners import AdaptiveFake
from prototypes.adaptive_assessment.synthetic import admit


@dataclass
class Collected:
    run: AgentRun
    executor: AdaptiveExecutor
    audit: dict


class FixedInvestigation:
    """A declared deterministic schedule, not a model or an oracle-dependent policy."""

    offline_only = True

    async def decide(self, prompt):
        view = json.loads(prompt)
        facts = view["facts"]
        selected = [f["fact_id"] for f in facts if f["qualified"]]

        def finish():
            return {
                "name": "finish",
                "arguments": {
                    "selected_fact_ids": selected[:3],
                    "reason": "completed" if selected else "no_valid_findings",
                },
            }

        if not facts:
            decision = {"name": "get_dimension_statistics", "arguments": {}}
        else:
            meta = {i["item_id"]: i for i in view["analysis_context"]["items"]}
            items = [
                i
                for i in facts[0].get("items", [])
                if i.get("has_variance")
                and i.get("state") == "available"
                and meta[i["item_id"]]["relation_eligible"]
                and len(i.get("exploration_thresholds", [])) >= 2
            ]
            if len(items) < 2 or view["last_result"]["status"] == "rejected":
                decision = finish()
            elif not any(f.get("type") == "item_relation" for f in facts):
                decision = {
                    "name": "query_dimension_relation",
                    "arguments": {"left": items[0]["item_id"], "right": items[1]["item_id"]},
                }
            elif not any(f.get("type") == "item_group_difference" for f in facts):
                decision = {
                    "name": "compare_student_groups",
                    "arguments": {
                        "conditions": [
                            {
                                "item_id": items[0]["item_id"],
                                "operator": "<=",
                                "threshold": items[0]["exploration_thresholds"][0],
                            }
                        ],
                        "outcomes": [items[1]["item_id"]],
                    },
                }
            else:
                decision = finish()
        return json.dumps(decision)


def proposal_valid(decision, view):
    """Independent domain preconditions, not a claim of full JSON Schema validation."""
    name, args = decision.get("name"), decision.get("arguments")
    if name not in SCHEMAS or not isinstance(args, dict):
        return False
    ids = {i["item_id"]: i for i in view["analysis_context"]["items"]}
    known = {f["fact_id"] for f in view["facts"]}
    if any(i not in known for i in decision.get("basis_fact_ids", [])):
        return False
    if name == "query_dimension_relation":
        return (
            set(args) == {"left", "right"}
            and args["left"] != args["right"]
            and all(i in ids and ids[i]["relation_eligible"] for i in (args["left"], args["right"]))
        )
    if name == "compare_student_groups":
        conditions = args.get("conditions", [])
        outcomes = args.get("outcomes", [])
        for p in conditions:
            if (
                not isinstance(p, dict)
                or set(p) != {"item_id", "operator", "threshold"}
                or p.get("item_id") not in ids
                or p.get("operator") not in {"<", "<=", "==", ">=", ">"}
                or not isinstance(p.get("threshold"), str)
                or not re.fullmatch(r"[+-]?[0-9]{1,5}(?:\.[0-9]{1,2})?", p["threshold"])
            ):
                return False
            scale = ids[p["item_id"]]["scale"]
            if scale is None:
                return False
            value = Fraction(p["threshold"])
            if (
                not Fraction(scale["minimum"]) <= value <= Fraction(scale["maximum"])
                or ((value - Fraction(scale["minimum"])) / Fraction(scale["step"])).denominator != 1
            ):
                return False
        return (
            set(args) == {"conditions", "outcomes"}
            and 1 <= len(conditions) <= 2
            and len(outcomes) == 1
            and len({p.get("item_id") for p in conditions}) == len(conditions)
            and all(
                p.get("item_id") in ids and ids[p["item_id"]]["filter_eligible"] for p in conditions
            )
            and outcomes[0] in ids
            and ids[outcomes[0]]["relation_eligible"]
            and outcomes[0] not in {p.get("item_id") for p in conditions}
        )
    if name == "inspect_discovery_support":
        return set(args) == {"fact_id"} and args["fact_id"] in known
    if name == "finish":
        return set(args) == {"selected_fact_ids", "reason"} and all(
            i in known for i in args["selected_fact_ids"]
        )
    if name == "inspect_item_quality":
        return set(args) == {"item_id"} and args["item_id"] in ids
    return not args or (
        set(args) == {"item_ids"}
        and (args["item_ids"] is None or all(i in ids for i in args["item_ids"]))
    )


async def collect(
    recipe: str,
    state_dir: Path,
    *,
    profile="http_mock",
    fault=None,
    authorization_overrides=None,
    response_mutator=None,
) -> Collected:
    """Calls the existing typed Planner and Executor, retaining local proof objects.

    Unlike run_synthetic_live's JSON projection, the local executor is retained solely
    for independent source scoring. There is intentionally no live/network option.
    """
    if profile not in ("http_mock", "fixed"):
        raise ValueError("real_calls_disabled_in_acceptance")
    source = admit(recipe)
    source.validate()
    executor = AdaptiveExecutor(source, "run-" + uuid4().hex[:16], Limits(timeout_seconds=30))
    requests = []
    proposals = []
    policy = AdaptiveFake()

    def handler(request):
        payload = json.loads(request.content)
        view = json.loads(payload["messages"][-1]["content"])
        # Record actual remote-bound data only; no fixture/target label is added.
        requests.append(
            {
                "round": len(requests) + 1,
                "new_message": payload["messages"][-1],
                "history_length": len(payload["messages"]),
                "tools": [t["function"]["name"] for t in payload["tools"]],
                "tool_schemas": payload["tools"],
                "protocol": {
                    k: payload[k]
                    for k in (
                        "model",
                        "max_tokens",
                        "tool_choice",
                        "parallel_tool_calls",
                        "enable_thinking",
                    )
                },
            }
        )
        chosen = policy.choose(view)
        if fault:
            chosen = fault(chosen, len(requests), view)
        body = native_response(chosen, len(requests) - 1)
        if response_mutator:
            body = response_mutator(body, len(requests), view)
        try:
            call = body["choices"][0]["message"]["tool_calls"][0]["function"]
            arguments = json.loads(call["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError
            actual = {
                "name": call["name"],
                "arguments": {k: v for k, v in arguments.items() if k != "basis_fact_ids"},
                "basis_fact_ids": arguments.get("basis_fact_ids", []),
            }
            legal = proposal_valid(actual, view)
        except (ValueError, KeyError, TypeError):
            actual = {"status": "malformed_native_response"}
            legal = False
        proposals.append(
            {"round": len(requests), "decision": actual, "legal_domain_request": legal}
        )
        return httpx.Response(200, json=body)

    budget = None
    if profile == "http_mock":
        authorization = mock_authorization(
            recipe, campaign_id="eval-" + uuid4().hex, **(authorization_overrides or {})
        )
        budget = CampaignBudget(authorization, state_dir, origin="http_mock", today=MOCK_DATE)
        planner = QwenAdaptivePlanner(executor, budget, transport=httpx.MockTransport(handler))
    else:
        planner = FixedInvestigation()
    profiler = cProfile.Profile()
    start = perf_counter()
    try:
        with profiler:
            run = await _run_session(
                executor,
                planner,
                limits=executor.limits,
                decision_type=Decision,
                argument_parser=executor.parse,
                analysis_context=executor.kernel.catalog(),
            )
        counts = {}
        for entry in profiler.getstats():
            code = entry.code
            if hasattr(code, "co_filename") and code.co_name in (
                "statistics",
                "_pair",
                "group",
                "quality",
                "_verify",
                "derive",
                "_gate",
            ):
                path = Path(code.co_filename)
                if "adaptive_assessment" in path.parts:
                    key = path.name + ":" + code.co_name
                    counts[key] = counts.get(key, 0) + entry.callcount
        audit = {
            "profile": profile,
            "planner_class": type(planner).__module__ + "." + type(planner).__qualname__,
            "measurement_origin": "offline_mechanism" if budget else "offline_deterministic",
            "source_fingerprint": source.fingerprint,
            "runtime_batch_id": executor.kernel.data.batch.batch_id,
            "snapshot_id": source.fingerprint,
            "run_id": run.run_id,
            "status": run.status,
            "reason": run.reason,
            "http_transcript": requests,
            "model_proposals": proposals,
            "planner_choices": getattr(planner, "choices", []),
            "tool_ledger": [asdict(t) for t in run.tools],
            "full_trace": [asdict(t) for t in run.trace],
            "issued_facts": [f.view for f in run.facts],
            "proposed_ids": run.proposed_ids,
            "selected_ids": run.selected_ids,
            "cards": run.cards,
            "tool_attempts": run.tool_attempts,
            "policy_decisions": run.model_requests,
            "model_requests": len(requests),
            "elapsed_seconds": perf_counter() - start,
            "operation_counts": counts,
            "budget": budget.snapshot() if budget else None,
            "actual_tokens": None,
            "actual_cost_cny": None,
            "paid_api_calls": 0,
        }
        return Collected(run, executor, wire(audit))
    finally:
        if budget:
            await planner.aclose()
            budget.close()
