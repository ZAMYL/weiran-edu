"""Capability D adapter: score a recorded real-Qwen investigation trace.

D is the future real-model pipeline (autonomous direction choice, tool calls,
evidence-backed answers). This module never calls a model. It replays an
already-recorded decision trace through the *real* C-01 executor so directions,
Witness binding, budgets and abstention are checked by the same kernel, then
scores with the unchanged DS-06A scorer.

Scoring convention: D is scored against C's human standard (both are
tool-investigation pipelines). The oracle files are never modified; the adapter
only relabels a shallow copy for ``score_result``.

Trace grounding uses stable *roles* (``overview``, ``inspect:<n>``) rather than
random per-run Fact IDs, so a recorded trace can be replayed deterministically.
A real-model recorder maps its live Fact IDs to these roles at capture time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from prototypes.research_agent_v2.programming import INSPECT, OVERVIEW

from app.ai.edu.discovery.models import AgentError, Limits

from .c01 import _root_assertions, _run_problem, _source
from .common import rejection, result_shell
from .metrics import score_result
from .model import Case, load_oracle
from .pipelines import build_ds03

_FORBIDDEN_KEY_PREFIXES = ("expected", "oracle", "human_", "root_cause")
_TOOL_NAMES = frozenset({OVERVIEW, INSPECT})


@dataclass(frozen=True)
class AgentStep:
    """One recorded model decision plus its measured usage."""

    decision: dict
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


@dataclass(frozen=True)
class AgentTrace:
    """A recorded autonomous investigation for one problem.

    ``origin`` must be ``approved_qwen``; an offline selector trace is rejected
    so fixed offline actions cannot masquerade as model reasoning.
    """

    origin: str
    problem: str
    steps: tuple[AgentStep, ...]
    final_answer: dict | str = ""
    cost: float | None = None


def run_model_agent(case: Case, trace: AgentTrace, data_dir: Path) -> dict:
    """Validate and score one recorded D trace; returns a DS-06A result dict."""
    try:
        _validate_trace(trace)
    except AgentError as error:
        return rejection(case.case_id, "D", str(error))

    ds03, _ = build_ds03(case)
    if ds03["status"] == "rejected":
        return rejection(case.case_id, "D", ds03["error"])
    result = result_shell(case.case_id, "D")
    for key in (
        "statistics",
        "features",
        "feature_status",
        "candidates",
        "feature_cohorts",
        "root_assertions",
    ):
        result[key] = ds03[key]
    result["root_assertions"] += _root_assertions(trace.final_answer)
    result["final_answer"] = trace.final_answer
    result["model_usage"] = _usage(trace)

    violations: list[str] = []
    try:
        source = _source(case, ds03["feature_cohorts"])
        with TemporaryDirectory(prefix="ds06b-trace-") as directory:
            _run_problem(
                case,
                source,
                trace.problem,
                Path(directory),
                result,
                decide=_trace_decider(trace, violations),
            )
    except (AgentError, ValueError) as error:
        return rejection(case.case_id, "D", violations[0] if violations else str(error))
    if violations:
        return rejection(case.case_id, "D", violations[0])
    result["source_verified"] = True

    # Reuse the unchanged scorer against C's human standard; no oracle edits.
    scored = dict(result)
    scored["system"] = "C"
    result["scored_as"] = "C"
    result["metrics"] = score_result(case, load_oracle(data_dir, case), scored)
    return result


def _validate_trace(trace: AgentTrace) -> None:
    if trace.origin != "approved_qwen":
        raise AgentError("model_origin_required")
    if not trace.steps:
        raise AgentError("trace_incomplete")
    limits = Limits()
    if len(trace.steps) > limits.model_requests:
        raise AgentError("model_budget_exceeded")
    if sum(step.decision.get("name") in _TOOL_NAMES for step in trace.steps) > limits.tool_attempts:
        raise AgentError("tool_budget_exceeded")
    if _leaks_oracle(trace.final_answer, ("expected", "oracle", "human_")) or any(
        _leaks_oracle(step.decision) for step in trace.steps
    ):
        raise AgentError("oracle_leakage")


def _leaks_oracle(value, prefixes: tuple[str, ...] = _FORBIDDEN_KEY_PREFIXES) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower().startswith(prefixes) or _leaks_oracle(child, prefixes)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_leaks_oracle(item, prefixes) for item in value)
    return False


def _usage(trace: AgentTrace) -> dict:
    return {
        "origin": trace.origin,
        "model_requests": len(trace.steps),
        "tool_attempts": sum(step.decision.get("name") in _TOOL_NAMES for step in trace.steps),
        "input_tokens": sum(step.input_tokens for step in trace.steps),
        "output_tokens": sum(step.output_tokens for step in trace.steps),
        "cost": trace.cost,
        "latency_ms": sum(step.latency_ms for step in trace.steps),
    }


def _trace_decider(trace: AgentTrace, violations: list[str]):
    """Drive the real executor from the recorded trace, checking grounding.

    Decisions are matched by tool semantics (direction key / index), never by
    random Fact IDs. A step may only cite roles already disclosed by the tools.
    Violations are recorded because the executor converts transport-handler
    exceptions into a generic model_request_failed reason.
    """
    index = 0
    disclosed: list[str] = []

    def fail(code: str):
        violations.append(code)
        raise AgentError(code)

    def decide(facts: list[dict]) -> dict:
        nonlocal index
        if facts:
            disclosed.append("overview" if len(disclosed) == 0 else f"inspect:{len(disclosed) - 1}")
        if index >= len(trace.steps):
            fail("trace_incomplete")
        step = trace.steps[index]
        index += 1
        decision = step.decision
        name = decision.get("name")
        basis = decision.get("basis_fact_ids", [])
        if not set(basis) <= set(disclosed):
            fail("ungrounded_decision_basis")
        if name == OVERVIEW:
            return {"name": OVERVIEW, "arguments": {}}
        if name == INSPECT:
            overview = facts[0]
            directions = overview.get("directions", [])
            arguments = decision.get("arguments", {})
            if "direction" in arguments:
                direction = arguments["direction"]
                if direction not in [item["direction"] for item in directions]:
                    fail("invalid_direction")
            else:
                position = arguments.get("direction_index")
                if not isinstance(position, int) or not 0 <= position < len(directions):
                    fail("invalid_direction")
                direction = directions[position]["direction"]
            return {
                "name": INSPECT,
                "arguments": {"overview_id": overview["fact_id"], "direction": direction},
                "basis_fact_ids": [overview["fact_id"]],
            }
        if name == "finish":
            selected = [fact["fact_id"] for fact in facts[1:] if fact.get("qualified")]
            reason = decision.get("arguments", {}).get("reason")
            if reason not in {"completed", "insufficient_data"}:
                fail("invalid_finish_reason")
            return {
                "name": "finish",
                "arguments": {"selected_fact_ids": selected, "reason": reason},
                "basis_fact_ids": [fact["fact_id"] for fact in facts],
            }
        fail("unknown_tool_in_trace")

    return decide
