"""Shared offline session: one budget, typed tools and verified facts.

Trusted executors admit sources and project aggregates; no route exposes injection.
"""

import asyncio
import json
from time import perf_counter

from pydantic import ValidationError

from ..models import InsightError
from ..qwen import _unique_pairs
from .models import AgentError, AgentRun, Decision, Event, Limits, Planner, ToolEntry
from .tools import parse_arguments


async def run_session(
    executor,
    planner: Planner,
    *,
    limits: Limits,
    decision_type=Decision,
    argument_parser=parse_arguments,
    analysis_context: dict | None = None,
) -> AgentRun:
    if getattr(planner, "offline_only", False) is not True:
        raise AgentError("remote_discovery_disabled")
    run_id = executor.run_id
    trace = []
    tools = []
    model_requests = tool_attempts = 0
    started = perf_counter()
    last_result = None
    proposed_ids = ()

    def event(phase, status, tool=None, ids=(), error=None):
        trace.append(Event(len(trace) + 1, model_requests, phase, status, tool, tuple(ids), error))

    def result(status, reason, cards=(), selected=()):
        return AgentRun(
            run_id,
            status,
            reason,
            tuple(cards),
            tuple(selected),
            tuple(executor.facts.values()),
            tuple(tools),
            tuple(trace),
            model_requests,
            tool_attempts,
            perf_counter() - started,
            proposed_ids=proposed_ids,
            tool_compute_seconds=executor.compute_seconds,
        )

    while model_requests < limits.model_requests:
        remaining_seconds = limits.run_seconds - (perf_counter() - started)
        if remaining_seconds <= 0:
            event("planning", "rejected", error="run_timeout")
            return result("failed", "run_timeout")
        view = {
            "instruction": (
                "Choose one typed investigation or finish; select only current verified fact IDs. "
                "No arithmetic, prose, causal claims, identities or evidence requests. "
                "Thresholds are exploratory, not teaching standards."
            ),
            "question": (
                "Explore verifiable aggregate scoring patterns; no finding is acceptable."
            ),
            "remaining_model_requests": limits.model_requests - model_requests,
            "remaining_tool_attempts": limits.tool_attempts - tool_attempts,
            "facts": [f.view for f in executor.facts.values()],
            "last_result": last_result,
        }
        if analysis_context is not None:
            view["analysis_context"] = analysis_context
        prompt = json.dumps(view, ensure_ascii=False, allow_nan=False)
        if len(prompt.encode()) > limits.prompt_bytes:
            event("planning", "rejected", error="prompt_size_limit")
            return result("failed", "prompt_size_limit")
        model_requests += 1  # A timeout or client recreation cannot restore this counter.
        event("planning", "requested")
        try:
            async with asyncio.timeout(min(limits.timeout_seconds, remaining_seconds)):
                raw = await planner.decide(prompt)
            if not isinstance(raw, str) or len(raw.encode()) > 16384:
                raise AgentError("invalid_model_response")
            value = json.loads(
                raw,
                object_pairs_hook=_unique_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            decision = decision_type.model_validate(value)
            if len(set(decision.basis_fact_ids)) != len(decision.basis_fact_ids) or any(
                f not in executor.facts for f in decision.basis_fact_ids
            ):
                raise AgentError("unknown_fact_id")
            event("model_selection", "accepted", decision.name, decision.basis_fact_ids)
            if decision.name == "finish":
                model = argument_parser("finish", decision.arguments)
                proposed_ids = tuple(model.selected_fact_ids)
                cards = executor.finish(model)
                event("final_selection", "accepted", "finish", model.selected_fact_ids)
                return result("completed", model.reason, cards, model.selected_fact_ids)
            if tool_attempts >= limits.tool_attempts:
                raise AgentError("tool_budget_exhausted")
            tool_attempts += 1
            event("tool_execution", "attempted", decision.name)
            try:
                args = argument_parser(decision.name, decision.arguments)
                facts = executor.execute(decision.name, args)
                ids = tuple(f.fact_id for f in facts)
                tools.append(
                    ToolEntry(
                        tool_attempts, decision.name, "accepted", ids, arguments=args.model_dump()
                    )
                )
                event("tool_execution", "computed", decision.name)
                event("fact_validation", "accepted", decision.name, ids)
                last_result = {"tool": decision.name, "status": "accepted", "fact_ids": ids}
            except AgentError as exc:
                code = str(exc)
                tools.append(ToolEntry(tool_attempts, decision.name, "rejected", error=code))
                event("fact_validation", "rejected", decision.name, error=code)
                last_result = {"tool": decision.name, "status": "rejected", "error": code}
                if code in {
                    "repeated_tool",
                    "duplicate_discovery",
                    "source_scope_mismatch",
                    "tool_output_limit",
                }:
                    return result("failed", code)
        except asyncio.CancelledError:
            event("planning", "cancelled", error="cancelled")
            raise  # Cooperative cancellation, never a pseudo-success result.
        except (TimeoutError, InsightError) as exc:
            code = (
                "model_timeout"
                if isinstance(exc, TimeoutError) or str(exc) == "model_timeout"
                else "model_request_failed"
            )
            event("planning", "rejected", error=code)
            return result("failed", code)
        except (ValidationError, ValueError, TypeError, RecursionError) as exc:
            code = str(exc) if isinstance(exc, AgentError) else "invalid_model_response"
            event("validation", "rejected", error=code)
            return result("failed", code)
    event("planning", "rejected", error="model_budget_exhausted")
    return result("failed", "model_budget_exhausted")
