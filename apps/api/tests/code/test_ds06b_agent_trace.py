"""DS-06B: synthetic trace tests for the future real-Qwen (D) evaluation adapter.

No real model is called. Traces are synthetic fixtures replayed through the real
C-01 executor; the offline selector is used only to *record* a plausible decision
path for the positive fixture, and an offline-origin trace must be rejected.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from prototypes.research_agent_v2.programming import INSPECT, OVERVIEW

from evaluation.ds06a import c01 as c01mod
from evaluation.ds06a.agent_trace import AgentStep, AgentTrace, _trace_decider, run_model_agent
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
            role_by_id = {
                fact["fact_id"]: ("overview" if i == 0 else f"inspect:{i - 1}")
                for i, fact in enumerate(facts)
            }
            mapped = {
                "name": "finish",
                "arguments": {
                    "reason": decision["arguments"]["reason"],
                    "selected_roles": [
                        role_by_id[fid] for fid in decision["arguments"]["selected_fact_ids"]
                    ],
                },
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
    assert result["model_origin_claim"] == "approved_qwen"
    assert result["model_origin_verified"] is False
    usage = result["model_usage"]
    assert usage["origin_claim"] == "approved_qwen"
    assert usage["origin_verified"] is False
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
                "arguments": {"reason": "insufficient_data", "selected_roles": []},
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


def _with_finish(trace: AgentTrace, arguments: dict) -> AgentTrace:
    finish = AgentStep(
        {
            "name": "finish",
            "arguments": arguments,
            "basis_fact_ids": ["overview"] + [f"inspect:{i}" for i in range(len(trace.steps) - 2)],
        }
    )
    return replace(trace, steps=trace.steps[:-1] + (finish,))


def test_empty_evidence_selection_is_not_autofilled() -> None:
    """Model selects no evidence: the adapter must not auto-fill and inflate."""
    case = case_by_id("association_not_causation")
    trace = _with_finish(
        _recorded_trace(case, case.problems[0]),
        {"reason": "completed", "selected_roles": []},
    )
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "missing_evidence_selection"


def test_missing_evidence_selection_field_is_rejected() -> None:
    case = case_by_id("association_not_causation")
    trace = _with_finish(_recorded_trace(case, case.problems[0]), {"reason": "completed"})
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "missing_evidence_selection"


def test_undisclosed_or_invalid_evidence_reference_is_rejected() -> None:
    case = case_by_id("association_not_causation")
    base = _recorded_trace(case, case.problems[0])
    for roles in (["inspect:9"], ["overview"], ["inspect:0", "inspect:0"]):
        trace = _with_finish(base, {"reason": "completed", "selected_roles": roles})
        result = run_model_agent(case, trace, DATA_DIR)
        assert result["status"] == "rejected", roles
        assert result["error"] == "invalid_evidence_reference", roles


def test_unqualified_evidence_reference_is_rejected() -> None:
    """Unit-level: the programming executor always discloses qualified inspect
    facts, so the unqualified guard is exercised by driving the decider directly
    with a synthetic unqualified fact."""
    steps = (
        AgentStep({"name": OVERVIEW, "arguments": {}}),
        AgentStep(
            {
                "name": INSPECT,
                "arguments": {"direction": "d0"},
                "basis_fact_ids": ["overview"],
            }
        ),
        AgentStep(
            {
                "name": "finish",
                "arguments": {"reason": "completed", "selected_roles": ["inspect:0"]},
                "basis_fact_ids": ["overview", "inspect:0"],
            }
        ),
    )
    trace = AgentTrace("approved_qwen", "p1", steps)
    violations: list[str] = []
    decide = _trace_decider(trace, violations, {"consumed": 0})
    facts = [
        {"fact_id": "f0", "directions": [{"direction": "d0"}]},
        {"fact_id": "f1", "qualified": False},
    ]
    decide([])
    decide(facts[:1])
    try:
        decide(facts)
    except Exception:
        pass
    assert violations == ["invalid_evidence_reference"]


def test_unconsumed_extra_steps_are_rejected() -> None:
    case = case_by_id("association_not_causation")
    trace = _recorded_trace(case, case.problems[0])
    trace = replace(trace, steps=trace.steps + (AgentStep({"name": OVERVIEW, "arguments": {}}),))
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "rejected"
    assert result["error"] == "unconsumed_trace_steps"


def test_relabelled_origin_never_yields_verified_provenance() -> None:
    """An offline-recorded trace relabelled approved_qwen may replay, but the
    result must never claim verified real-model origin."""
    case = case_by_id("association_not_causation")
    trace = _recorded_trace(case, case.problems[0])
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "ok", result["error"]
    assert result["model_origin_verified"] is False
    assert result["model_usage"]["origin_verified"] is False


# --- DS-06B-FIX-02: malformed model traces must be stably rejected -----------


def _base_trace() -> AgentTrace:
    case = case_by_id("association_not_causation")
    return _recorded_trace(case, case.problems[0])


def _assert_rejected(trace: AgentTrace, expected: str) -> None:
    case = case_by_id("association_not_causation")
    result = run_model_agent(case, trace, DATA_DIR)
    assert result["status"] == "rejected", result
    assert result["error"] == expected, result


@pytest.mark.parametrize("bad_role", [{}, [], None, 3, True])
def test_malformed_selected_role_elements(bad_role) -> None:
    trace = _with_finish(_base_trace(), {"reason": "completed", "selected_roles": [bad_role]})
    _assert_rejected(trace, "invalid_evidence_reference")


@pytest.mark.parametrize("bad_roles", ["inspect:0", None, 5, {}])
def test_malformed_selected_roles_container(bad_roles) -> None:
    trace = _with_finish(_base_trace(), {"reason": "completed", "selected_roles": bad_roles})
    _assert_rejected(trace, "missing_evidence_selection")


@pytest.mark.parametrize("bad_basis", ["overview", [{}], [[]], [None], [1], None])
def test_malformed_basis_fact_ids(bad_basis) -> None:
    trace = _base_trace()
    first = replace(
        trace.steps[0], decision={**trace.steps[0].decision, "basis_fact_ids": bad_basis}
    )
    _assert_rejected(replace(trace, steps=(first,) + trace.steps[1:]), "invalid_trace_structure")


@pytest.mark.parametrize("bad_name", [None, 5, {}, [], "bogus_tool"])
def test_malformed_tool_name(bad_name) -> None:
    trace = _base_trace()
    first = replace(trace.steps[0], decision={**trace.steps[0].decision, "name": bad_name})
    _assert_rejected(replace(trace, steps=(first,) + trace.steps[1:]), "invalid_trace_structure")


@pytest.mark.parametrize("bad_arguments", [None, [], "x", 5])
def test_malformed_arguments_container(bad_arguments) -> None:
    trace = _base_trace()
    first = replace(
        trace.steps[0], decision={**trace.steps[0].decision, "arguments": bad_arguments}
    )
    _assert_rejected(replace(trace, steps=(first,) + trace.steps[1:]), "invalid_trace_structure")


@pytest.mark.parametrize("bad_direction", [{}, [], None, 5])
def test_malformed_direction_value(bad_direction) -> None:
    trace = _base_trace()
    inspect = replace(
        trace.steps[1],
        decision={**trace.steps[1].decision, "arguments": {"direction": bad_direction}},
    )
    _assert_rejected(
        replace(trace, steps=(trace.steps[0], inspect) + trace.steps[2:]), "invalid_direction"
    )


@pytest.mark.parametrize("bad_index", ["0", True, 1.5, None, {}])
def test_malformed_direction_index(bad_index) -> None:
    trace = _base_trace()
    inspect = replace(
        trace.steps[1],
        decision={**trace.steps[1].decision, "arguments": {"direction_index": bad_index}},
    )
    _assert_rejected(
        replace(trace, steps=(trace.steps[0], inspect) + trace.steps[2:]), "invalid_direction"
    )


@pytest.mark.parametrize("bad_reason", [None, 5, {}, []])
def test_malformed_finish_reason(bad_reason) -> None:
    trace = _with_finish(_base_trace(), {"reason": bad_reason, "selected_roles": ["inspect:0"]})
    _assert_rejected(trace, "invalid_finish_reason")


@pytest.mark.parametrize("bad_tokens", [-1, "12", None, True, 1.5])
def test_malformed_token_counts(bad_tokens) -> None:
    trace = _base_trace()
    first = replace(trace.steps[0], input_tokens=bad_tokens)
    _assert_rejected(replace(trace, steps=(first,) + trace.steps[1:]), "invalid_usage_metrics")


@pytest.mark.parametrize("bad_latency", [-1.0, "x", None, True])
def test_malformed_latency(bad_latency) -> None:
    trace = _base_trace()
    first = replace(trace.steps[0], latency_ms=bad_latency)
    _assert_rejected(replace(trace, steps=(first,) + trace.steps[1:]), "invalid_usage_metrics")


@pytest.mark.parametrize("bad_cost", [-0.5, "free", {}, True])
def test_malformed_cost(bad_cost) -> None:
    _assert_rejected(replace(_base_trace(), cost=bad_cost), "invalid_usage_metrics")


@pytest.mark.parametrize(
    "bad_steps",
    [
        {"a": 1},
        "steps",
        ({"name": OVERVIEW},),
        (AgentStep("not-a-dict"),),
    ],
)
def test_malformed_steps_container(bad_steps) -> None:
    _assert_rejected(replace(_base_trace(), steps=bad_steps), "invalid_trace_structure")


def test_empty_steps_rejected() -> None:
    _assert_rejected(replace(_base_trace(), steps=()), "trace_incomplete")


@pytest.mark.parametrize("bad_problem", [None, 5, {}])
def test_malformed_problem_field(bad_problem) -> None:
    _assert_rejected(replace(_base_trace(), problem=bad_problem), "invalid_trace_structure")


@pytest.mark.parametrize("bad_answer", [5, None, []])
def test_malformed_final_answer(bad_answer) -> None:
    _assert_rejected(replace(_base_trace(), final_answer=bad_answer), "invalid_trace_structure")


def test_inspect_without_overview_is_rejected() -> None:
    case = case_by_id("association_not_causation")
    steps = (AgentStep({"name": INSPECT, "arguments": {"direction_index": 0}}),)
    _assert_rejected(AgentTrace("approved_qwen", case.problems[0], steps), "missing_overview_step")


def test_finish_without_overview_is_rejected() -> None:
    case = case_by_id("association_not_causation")
    steps = (
        AgentStep(
            {"name": "finish", "arguments": {"reason": "insufficient_data", "selected_roles": []}}
        ),
    )
    _assert_rejected(AgentTrace("approved_qwen", case.problems[0], steps), "missing_overview_step")
