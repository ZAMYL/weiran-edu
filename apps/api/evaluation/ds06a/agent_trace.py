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

Trust boundary: ``origin`` is a caller *claim*, not proof of provenance. A
passing replay only means "trace replay passed"; it never means "real Qwen
origin verified". Until a trusted live recorder exists, results carry
``model_origin_verified=False`` and no verified-origin conclusion may be drawn.

Evidence selection is the model's own decision: the finish step must explicitly
list disclosed, qualified evidence roles. The adapter never auto-fills evidence.
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

    ``origin`` must be declared ``approved_qwen`` for replay, but this is only a
    caller claim: it filters obviously offline traces and never certifies real
    model provenance (see ``model_origin_verified`` in the result).
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
    progress = {"consumed": 0}
    try:
        source = _source(case, ds03["feature_cohorts"])
        with TemporaryDirectory(prefix="ds06b-trace-") as directory:
            _run_problem(
                case,
                source,
                trace.problem,
                Path(directory),
                result,
                decide=_trace_decider(trace, violations, progress),
            )
    except (AgentError, ValueError) as error:
        return rejection(case.case_id, "D", violations[0] if violations else str(error))
    if violations:
        return rejection(case.case_id, "D", violations[0])
    if progress["consumed"] != len(trace.steps):
        return rejection(case.case_id, "D", "unconsumed_trace_steps")
    result["source_verified"] = True
    # Origin is a caller claim; replay success is not provenance verification.
    result["model_origin_claim"] = trace.origin
    result["model_origin_verified"] = False

    # Reuse the unchanged scorer against C's human standard; no oracle edits.
    scored = dict(result)
    scored["system"] = "C"
    result["scored_as"] = "C"
    result["metrics"] = score_result(case, load_oracle(data_dir, case), scored)
    return result


def _validate_trace(trace: AgentTrace) -> None:
    """Strict structural validation: malformed traces get stable rejection
    codes here, never unhandled TypeError/KeyError/IndexError downstream."""
    if trace.origin != "approved_qwen":
        raise AgentError("model_origin_required")
    if not isinstance(trace.problem, str) or not trace.problem:
        raise AgentError("invalid_trace_structure")
    if not isinstance(trace.final_answer, (dict, str)):
        raise AgentError("invalid_trace_structure")
    if not isinstance(trace.steps, (tuple, list)):
        raise AgentError("invalid_trace_structure")
    if not trace.steps:
        raise AgentError("trace_incomplete")
    for step in trace.steps:
        _validate_step(step)
    limits = Limits()
    if len(trace.steps) > limits.model_requests:
        raise AgentError("model_budget_exceeded")
    if sum(step.decision["name"] in _TOOL_NAMES for step in trace.steps) > limits.tool_attempts:
        raise AgentError("tool_budget_exceeded")
    _validate_usage(trace)
    if _leaks_oracle(trace.final_answer, ("expected", "oracle", "human_")) or any(
        _leaks_oracle(step.decision) for step in trace.steps
    ):
        raise AgentError("oracle_leakage")


def _validate_step(step) -> None:
    if not isinstance(step, AgentStep) or not isinstance(step.decision, dict):
        raise AgentError("invalid_trace_structure")
    decision = step.decision
    name = decision.get("name")
    if not isinstance(name, str) or name not in {OVERVIEW, INSPECT, "finish"}:
        raise AgentError("invalid_trace_structure")
    arguments = decision.get("arguments", {})
    if not isinstance(arguments, dict):
        raise AgentError("invalid_trace_structure")
    basis = decision.get("basis_fact_ids", [])
    if not isinstance(basis, list) or any(not isinstance(item, str) for item in basis):
        raise AgentError("invalid_trace_structure")
    if name == INSPECT:
        if "direction" in arguments:
            if not isinstance(arguments["direction"], str):
                raise AgentError("invalid_direction")
        elif "direction_index" in arguments:
            position = arguments["direction_index"]
            if isinstance(position, bool) or not isinstance(position, int):
                raise AgentError("invalid_direction")
        else:
            raise AgentError("invalid_direction")
    if name == "finish":
        reason = arguments.get("reason")
        if not isinstance(reason, str) or reason not in {"completed", "insufficient_data"}:
            raise AgentError("invalid_finish_reason")
        if "selected_roles" in arguments:
            roles = arguments["selected_roles"]
            if not isinstance(roles, list):
                raise AgentError("missing_evidence_selection")
            if any(not isinstance(role, str) for role in roles):
                raise AgentError("invalid_evidence_reference")


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_measure(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _validate_usage(trace: AgentTrace) -> None:
    """Self-reported usage must at least be well-formed; it is never treated as
    independently verified billing."""
    for step in trace.steps:
        if not _is_count(step.input_tokens) or not _is_count(step.output_tokens):
            raise AgentError("invalid_usage_metrics")
        if not _is_measure(step.latency_ms):
            raise AgentError("invalid_usage_metrics")
    if trace.cost is not None and not _is_measure(trace.cost):
        raise AgentError("invalid_usage_metrics")


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
        "origin_claim": trace.origin,
        "origin_verified": False,
        "model_requests": len(trace.steps),
        "tool_attempts": sum(step.decision.get("name") in _TOOL_NAMES for step in trace.steps),
        "input_tokens": sum(step.input_tokens for step in trace.steps),
        "output_tokens": sum(step.output_tokens for step in trace.steps),
        "cost": trace.cost,
        "latency_ms": sum(step.latency_ms for step in trace.steps),
    }


def _trace_decider(trace: AgentTrace, violations: list[str], progress: dict):
    """Drive the real executor from the recorded trace, checking grounding.

    Decisions are matched by tool semantics (direction key / index), never by
    random Fact IDs. A step may only cite roles already disclosed by the tools.
    Evidence at finish must be explicitly selected by the model via disclosed,
    qualified ``inspect:<n>`` roles; nothing is auto-filled.
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
        progress["consumed"] = index
        decision = step.decision
        name = decision.get("name")
        basis = decision.get("basis_fact_ids", [])
        if not set(basis) <= set(disclosed):
            fail("ungrounded_decision_basis")
        if name == OVERVIEW:
            return {"name": OVERVIEW, "arguments": {}}
        if name == INSPECT:
            if not facts:
                fail("missing_overview_step")
            overview = facts[0]
            directions = overview.get("directions", [])
            arguments = decision.get("arguments", {})
            if "direction" in arguments:
                direction = arguments["direction"]
                if direction not in [item["direction"] for item in directions]:
                    fail("invalid_direction")
            else:
                position = arguments.get("direction_index")
                if (
                    isinstance(position, bool)
                    or not isinstance(position, int)
                    or not 0 <= position < len(directions)
                ):
                    fail("invalid_direction")
                direction = directions[position]["direction"]
            return {
                "name": INSPECT,
                "arguments": {"overview_id": overview["fact_id"], "direction": direction},
                "basis_fact_ids": [overview["fact_id"]],
            }
        if name == "finish":
            if not facts:
                fail("missing_overview_step")
            arguments = decision.get("arguments", {})
            reason = arguments.get("reason")
            if reason not in {"completed", "insufficient_data"}:
                fail("invalid_finish_reason")
            if "overview" not in disclosed:
                fail("missing_overview_step")
            if "selected_roles" not in arguments:
                fail("missing_evidence_selection")
            roles = arguments["selected_roles"]
            if not isinstance(roles, list):
                fail("missing_evidence_selection")
            if reason == "completed" and not roles:
                fail("missing_evidence_selection")
            if reason == "insufficient_data" and roles:
                fail("abstention_with_selected_evidence")
            if len(set(roles)) != len(roles):
                fail("invalid_evidence_reference")
            selected = []
            for role in roles:
                if role not in disclosed or not str(role).startswith("inspect:"):
                    fail("invalid_evidence_reference")
                fact = facts[disclosed.index(role)]
                if not fact.get("qualified"):
                    fail("invalid_evidence_reference")
                selected.append(fact["fact_id"])
            return {
                "name": "finish",
                "arguments": {"selected_fact_ids": selected, "reason": reason},
                "basis_fact_ids": [fact["fact_id"] for fact in facts],
            }
        fail("unknown_tool_in_trace")

    return decide
