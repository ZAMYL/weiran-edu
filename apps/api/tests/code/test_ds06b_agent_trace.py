"""DS-06B: synthetic trace tests for the future real-Qwen (D) evaluation adapter.

No real model is called. Traces are synthetic fixtures replayed through the real
C-01 executor; the offline selector is used only to *record* a plausible decision
path for the positive fixture, and an offline-origin trace must be rejected.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from prototypes.research_agent_v2.programming import INSPECT, OVERVIEW

from evaluation.ds06a import c01 as c01mod
from evaluation.ds06a.agent_trace import AgentStep, AgentTrace, run_model_agent
from evaluation.ds06a.common import result_shell
from evaluation.ds06a.model import load_cases
from evaluation.ds06a.pipelines import build_ds03

DATA_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "ds06a" / "data"


def case_by_id(case_id: str):
    return load_cases(DATA_DIR, case_id)[0]


def _recorded_trace(case, problem: str) -> AgentTrace:
    """Record a bounded decision path via the real executor as a synthetic fixture."""
    steps: list[AgentStep] = []

    def recording_decide(facts: list[dict]) -> dict:
        decision = c01mod._select_next_step(facts)
        disclosed = ["overview"] + [f"inspect:{i}" for i in range(max(len(facts) - 1, 0))]
        if decision["name"] == OVERVIEW:
            mapped = {"name": OVERVIEW, "arguments": {}}
        elif decision["name"] == INSPECT:
            mapped = {
                "name": INSPECT,
                "arguments": {"direction": decision["arguments"]["direction"]},
                "basis_fact_ids": ["overview"],
            }
        else:
            mapped = {
                "name": "finish",
                "arguments": {"reason": decision["arguments"]["reason"]},
                "basis_fact_ids": disclosed or ["overview"],
            }
        steps.append(AgentStep(mapped, input_tokens=12, output_tokens=6, latency_ms=2.0))
        return decision

    ds03, _ = build_ds03(case)
    source = c01mod._source(case, ds03["feature_cohorts"])
    with TemporaryDirectory(prefix="ds06b-fixture-") as directory:
        c01mod._run_problem(
            case,
            source,
            problem,
            Path(directory),
            result_shell(case.case_id, "fixture"),
            decide=recording_decide,
        )
    return AgentTrace("approved_qwen", problem, tuple(steps), {"summary": "evidence-backed"}, 0.01)


def test_valid_trace_scores_against_c_standard_with_usage() -> None:
    case = case_by_id("association_not_causation")
    trace = _recorded_trace(case, case.problems[0])
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "ok", result["error"]
    assert result["scored_as"] == "C"
    assert result["metrics"]["phenomenon_accuracy"] == 1.0
    assert result["metrics"]["unsupported_root_assertions"] == 0
    assert result["source_verified"] is True
    usage = result["model_usage"]
    assert usage["origin"] == "approved_qwen"
    assert usage["model_requests"] <= 6
    assert usage["tool_attempts"] <= 5
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0
    assert usage["cost"] == 0.01 and usage["latency_ms"] > 0


def test_offline_selector_origin_cannot_masquerade_as_model() -> None:
    case = case_by_id("association_not_causation")
    trace = replace(_recorded_trace(case, case.problems[0]), origin="offline_selector")
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "model_origin_required"


def test_oracle_fields_in_trace_are_rejected() -> None:
    case = case_by_id("association_not_causation")
    trace = replace(
        _recorded_trace(case, case.problems[0]),
        final_answer={"expected_phenomena": ["leaked"]},
    )
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "oracle_leakage"


def test_wrong_direction_is_rejected() -> None:
    case = case_by_id("association_not_causation")
    steps = (
        AgentStep({"name": OVERVIEW, "arguments": {}}),
        AgentStep(
            {
                "name": INSPECT,
                "arguments": {"direction": "not-a-real-direction"},
                "basis_fact_ids": ["overview"],
            }
        ),
        AgentStep({"name": "finish", "arguments": {"reason": "completed"}}),
    )
    trace = AgentTrace("approved_qwen", case.problems[0], steps)
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "invalid_direction"


def test_witness_mismatch_case_still_rejected() -> None:
    case = case_by_id("missing_witness")
    steps = (
        AgentStep({"name": OVERVIEW, "arguments": {}}),
        AgentStep(
            {
                "name": "finish",
                "arguments": {"reason": "completed"},
                "basis_fact_ids": ["overview"],
            }
        ),
    )
    trace = AgentTrace("approved_qwen", case.problems[0], steps)
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "source_scope_mismatch"


def test_model_request_budget_is_enforced() -> None:
    case = case_by_id("association_not_causation")
    steps = tuple(AgentStep({"name": OVERVIEW, "arguments": {}}) for _ in range(7))
    result = run_model_agent(case, AgentTrace("approved_qwen", case.problems[0], steps), DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "model_budget_exceeded"


def test_tool_attempt_budget_is_enforced() -> None:
    case = case_by_id("association_not_causation")
    steps = tuple(
        AgentStep({"name": INSPECT, "arguments": {"direction_index": 0}}) for _ in range(6)
    )
    result = run_model_agent(case, AgentTrace("approved_qwen", case.problems[0], steps), DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "tool_budget_exceeded"


def test_ungrounded_decision_basis_is_rejected() -> None:
    case = case_by_id("association_not_causation")
    steps = (
        AgentStep({"name": OVERVIEW, "arguments": {}}),
        AgentStep(
            {
                "name": INSPECT,
                "arguments": {"direction_index": 0},
                "basis_fact_ids": ["inspect:3"],
            }
        ),
    )
    result = run_model_agent(case, AgentTrace("approved_qwen", case.problems[0], steps), DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "ungrounded_decision_basis"


def test_correct_abstention_on_small_sample() -> None:
    case = case_by_id("small_sample_privacy")
    steps = (
        AgentStep({"name": OVERVIEW, "arguments": {}}),
        AgentStep(
            {
                "name": "finish",
                "arguments": {"reason": "insufficient_data"},
                "basis_fact_ids": ["overview"],
            }
        ),
    )
    trace = AgentTrace("approved_qwen", case.problems[0], steps)
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "ok", result["error"]
    assert not result["cards"]
    metrics = result["metrics"]
    assert metrics["abstention_total"] > 0
    assert metrics["abstention_success"] == metrics["abstention_total"]


def test_unsupported_causal_claim_is_counted() -> None:
    case = case_by_id("association_not_causation")
    trace = replace(
        _recorded_trace(case, case.problems[0]),
        final_answer={"root_cause": "missing boundary check caused every failure"},
    )
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "ok", result["error"]
    assert result["metrics"]["unsupported_root_assertions"] >= 1
