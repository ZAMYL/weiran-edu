"""Executable adaptive paths and source/privacy gates, not real Qwen quality."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from prototypes.adaptive_assessment.agent import AdaptiveExecutor, run_adaptive
from prototypes.adaptive_assessment.analysis import Kernel
from prototypes.adaptive_assessment.planners import (
    AdaptiveFake,
    AdaptiveMockPlanner,
    LiveAuthorization,
    mock_transport,
    prepare_synthetic_live,
)
from prototypes.adaptive_assessment.synthetic import admit

from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.discovery.orchestrator import run_agent
from app.ai.edu.discovery.tools import parse_arguments as old_arguments

PAIR = {"left": "Q01", "right": "Q02"}
GROUP = {
    "conditions": [{"item_id": "Q01", "operator": "<=", "threshold": "20"}],
    "outcomes": ["Q02"],
}


def finish(ids=()):
    return {
        "name": "finish",
        "arguments": {
            "selected_fact_ids": list(ids),
            "reason": "completed" if ids else "no_valid_findings",
        },
    }


class Scripted(AdaptiveFake):
    def __init__(self, *steps):
        self.steps = iter(steps)
        self.prompts = []

    async def decide(self, prompt):
        self.prompts.append(json.loads(prompt))
        result = next(self.steps)
        return result if isinstance(result, str) else json.dumps(result)


def run(case="contest13", planner=None, **kwargs):
    return asyncio.run(run_adaptive(case, planner or AdaptiveFake(), **kwargs))


def executor(case="contest13", **kwargs):
    return AdaptiveExecutor(admit(case), "synthetic-run", Limits(**kwargs))


def execute(e, tool, args=None):
    return e.execute(tool, e.parse(tool, args or {}))


def test_two_structures_choose_different_actual_investigations():
    five, _ = run("five")
    questions, _ = run()
    assert [t.tool for t in five.tools] == [
        "get_dimension_statistics",
        "query_dimension_relation",
        "compare_student_groups",
        "inspect_discovery_support",
    ]
    assert [t.tool for t in questions.tools] == [
        "get_dimension_statistics",
        "inspect_item_quality",
        "compare_student_groups",
        "inspect_discovery_support",
    ]
    assert (five.model_requests, five.tool_attempts) == (5, 4)
    assert (questions.model_requests, questions.tool_attempts) == (5, 4)
    a = next(c for c in five.cards if c["type"] == "item_group_difference")
    b = next(c for c in questions.cards if c["type"] == "item_group_difference")
    assert (
        a["metrics"]["target_count"],
        a["metrics"]["remainder_count"],
        a["metrics"]["difference"]["numerator"],
    ) == (26, 27, 4)
    assert (
        b["metrics"]["target_count"],
        b["metrics"]["remainder_count"],
        b["metrics"]["difference"]["numerator"],
    ) == (10, 10, 60)
    assert not any("radar" in json.dumps(c) for c in questions.cards)
    for result in (five, questions):
        assert {e.phase for e in result.trace} >= {
            "planning",
            "model_selection",
            "tool_execution",
            "fact_validation",
            "final_selection",
        }
        assert all(
            f.fact_id
            in {i for e in result.trace if e.phase == "fact_validation" for i in e.fact_ids}
            for f in result.facts
        )


def test_same_thirteen_item_metadata_different_actual_quality_changes_path():
    incomplete, _ = run()
    complete, _ = run("contest13_complete")
    assert incomplete.tools[1].tool == "inspect_item_quality"
    assert complete.tools[1].tool == "query_dimension_relation"
    # No recipe label, seed, expected answer or target is supplied to the decision policy.
    assert all(f.view.get("qualified") in (True, False) for f in complete.facts)


def test_small_data_safe_stop_and_partial_data_has_no_fabricated_values():
    small, _ = run("small")
    assert small.reason == "insufficient_data" and small.cards == ()
    partial, e = run("limited")
    assert partial.status == "completed"
    root = partial.facts[0].view
    absent = next(i for i in root["items"] if i["item_id"] == "Q11")
    unknown = next(i for i in root["items"] if i["item_id"] == "Q12")
    assert absent["valid_count"] == 0 and absent["mean"] is None
    assert unknown["range_checked"] is False
    with pytest.raises(AgentError, match="relation_scale_or_level_unknown"):
        execute(e, "query_dimension_relation", {"left": "Q12", "right": "Q01"})


def test_scoped_distribution_available_on_demand_under_original_output_limit():
    e = executor()
    fact = execute(e, "get_dimension_statistics", {"item_ids": ["Q01"]})[0]
    assert len(fact.view["items"]) == 1
    assert [b["count"] for b in fact.view["items"][0]["coarse_distribution"]] == [10, 0, 0, 10]
    assert e.output_bytes <= 8192


def test_same_tool_cumulative_disclosure_fix_is_shared():
    e = executor("privacy20")
    fact = execute(e, "get_dimension_statistics")[0]
    a = next(i for i in fact.view["items"] if i["item_id"] == "Q01")
    b = next(i for i in fact.view["items"] if i["item_id"] == "Q02")
    assert a["valid_count"] == 15
    assert b == {"item_id": "Q02", "state": "privacy_or_data_limited"}
    assert not any(s.item_id == "Q02" for s in fact.item_sources)


def test_native_http_mock_dynamic_schemas_and_no_private_content():
    async def exercise(case):
        catalog = Kernel(admit(case).envelope).catalog()
        client = AdaptiveMockPlanner(mock_transport(), catalog)
        try:
            result, e = await run_adaptive(case, client)
            assert result.status == "completed" and len(client.metrics) == result.model_requests
            text = json.dumps(client.history)
            for marker in (
                "S0000",
                "synthetic-member-0000",
                "Synthetic response only",
                "Synthetic rubric",
                "source_sha256",
                "locator",
                "evidence_id",
            ):
                assert marker not in text
            assert "Q13" in text if case == "contest13" else "Q13" not in text
            schema = client.schemas["query_dimension_relation"].model_json_schema()
            assert len(schema["properties"]["left"]["enum"]) == (13 if case == "contest13" else 5)
            return result
        finally:
            await client.aclose()

    a = asyncio.run(exercise("five"))
    b = asyncio.run(exercise("contest13"))
    assert a.tools[1].tool != b.tools[1].tool


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_dimension_statistics", {"batch_id": "foreign"}),
        ("query_dimension_relation", {"left": "ALGORITHMS", "right": "Q01"}),
        ("query_dimension_relation", {"left": "Q01", "right": "Q99"}),
        ("query_dimension_relation", {**PAIR, "sql": "SELECT private"}),
        ("inspect_item_quality", {"item_id": "Q99"}),
        (
            "compare_student_groups",
            {**GROUP, "conditions": [{"item_id": "Q01", "operator": "=", "threshold": "20"}]},
        ),
    ],
)
def test_bad_arguments_explicitly_rejected_without_input_echo(name, args):
    result, _ = run(planner=Scripted({"name": name, "arguments": args}, finish()))
    assert result.tools[0].error == "invalid_tool_arguments"
    assert result.tools[0].arguments == {} and not result.cards
    assert "SELECT private" not in repr(result.trace)


@pytest.mark.parametrize(
    "decision",
    [
        {"name": "execute_sql", "arguments": {}},
        {**finish(), "score": 100},
        {"name": "get_dimension_statistics", "arguments": {}, "basis_fact_ids": ["fake-id"]},
        finish(["foreign-run-f001"]),
        '{"name":"finish","name":"get_dimension_statistics","arguments":{}}',
    ],
)
def test_model_fabrication_and_unknown_references_are_failures(decision):
    result, _ = run(planner=Scripted(decision))
    assert result.status == "failed" and not result.cards


def test_failed_attempts_and_recreated_decisions_cannot_reset_run_budget():
    bad = {"name": "get_dimension_statistics", "arguments": {"batch_id": "foreign"}}
    result, _ = run(
        planner=Scripted(*([bad] * 5 + [{"name": "get_dimension_statistics", "arguments": {}}]))
    )
    assert (result.model_requests, result.tool_attempts) == (6, 5)
    assert result.reason == "tool_budget_exhausted"
    finished, _ = run(planner=Scripted(*([bad] * 5 + [finish()])))
    assert finished.status == "completed" and finished.model_requests == 6


def test_duplicates_stop_and_midway_finish_is_allowed():
    result, _ = run(
        planner=Scripted(
            {"name": "query_dimension_relation", "arguments": PAIR},
            {"name": "query_dimension_relation", "arguments": {"left": "Q02", "right": "Q01"}},
        )
    )
    assert result.reason == "repeated_tool" and result.tool_attempts == 2
    stopped, _ = run(
        planner=Scripted({"name": "query_dimension_relation", "arguments": PAIR}, finish())
    )
    assert stopped.status == "completed" and not stopped.cards and len(stopped.facts) == 1


def test_timeout_and_no_partial_fact_publication_on_size_limit():
    class Slow(AdaptiveFake):
        async def decide(self, prompt):
            await asyncio.sleep(0.05)
            return json.dumps(finish())

    result, _ = run(planner=Slow(), limits=Limits(timeout_seconds=0.001))
    assert result.reason == "model_timeout" and not result.cards
    e = executor(output_bytes=100)
    before = list(e.released)
    with pytest.raises(AgentError, match="tool_output_limit"):
        execute(e, "get_dimension_statistics")
    assert not e.facts and e.released == before and e.output_bytes == 0


def test_program_values_and_actual_metric_sources_are_replayed():
    e = executor()
    fact = execute(e, "compare_student_groups", GROUP)[0]
    target = e.resolve(fact.fact_id, "target.valid")
    assert len(target) == 10 and all(w.value == "80" and w.item_id == "Q02" for w in target)
    assert all(w.source_sha256 == e.source.fingerprint for w in target)
    wrong = replace(
        fact,
        item_sources=(
            replace(fact.item_sources[0], references=fact.item_sources[1].references),
            *fact.item_sources[1:],
        ),
    )
    e.facts[fact.fact_id] = wrong
    with pytest.raises(AgentError, match="source_validation_failed"):
        e.finish(e.parse("finish", finish([fact.fact_id])["arguments"]))


def test_tampered_math_rejected_before_fact_is_published():
    e = executor()
    actual = e.kernel.group(e.parse("compare_student_groups", GROUP).conditions, "Q02")
    corrupt = replace(actual, values={**actual.values, "difference": 999})
    before = list(e.released)
    with patch.object(e, "calculate", return_value=corrupt):
        with pytest.raises(AgentError, match="source_validation_failed"):
            execute(e, "compare_student_groups", GROUP)
    assert not e.facts and e.released == before and e.output_bytes == 0


def test_tampered_view_unknown_metric_and_cross_run_ids_rejected():
    e = executor()
    fact = execute(e, "compare_student_groups", GROUP)[0]
    with pytest.raises(AgentError, match="unknown_metric_key"):
        e.resolve(fact.fact_id, "invented")
    with pytest.raises(AgentError, match="unknown_fact_id"):
        executor().resolve(fact.fact_id, "target.valid")
    fact.view["metrics"]["difference"] = {"numerator": 999, "denominator": 1}
    with pytest.raises(AgentError, match="source_validation_failed"):
        e.resolve(fact.fact_id, "target.valid")


def test_deterministic_discovery_values_and_ids_with_unique_run_fact_ids():
    a, _ = run()
    b, _ = run()
    assert a.run_id != b.run_id
    assert [(c["discovery_id"], c["metrics"]) for c in a.cards] == [
        (c["discovery_id"], c["metrics"]) for c in b.cards
    ]


@pytest.mark.parametrize("mode", ["production", "synthetic_live"])
def test_remote_modes_remain_disabled(mode):
    with pytest.raises(AgentError, match="remote_discovery_disabled"):
        run(mode=mode)


def test_live_preparation_reuses_native_schemas_but_never_opens_network():
    source = admit("contest13")
    auth = LiveAuthorization(source.fingerprint, 6, 800, 10000, Decimal("1"))
    with patch(
        "app.ai.edu.qwen_config.QwenSettings.from_env",
        side_effect=AssertionError("must_not_read_credentials"),
    ):
        prepared = prepare_synthetic_live("contest13", auth)
    assert prepared["remote_enabled"] is prepared["production_enabled"] is False
    assert prepared["status"] == "prepared_remote_disabled" and prepared["max_requests"] == 6
    assert (
        prepared["cost_cap_cny"] == "1"
    )  # A declared test constraint, no actual spend or authorization.
    with pytest.raises(AgentError, match="invalid_live_authorization"):
        prepare_synthetic_live("contest13", replace(auth, source_fingerprint="wrong"))
    with pytest.raises(AgentError, match="invalid_live_authorization"):
        prepare_synthetic_live("contest13", replace(auth, max_requests=7))


def test_legacy_entry_does_not_mistake_questions_for_five_abilities():
    with pytest.raises(AgentError):
        old_arguments("query_dimension_relation", PAIR)
    with pytest.raises(AgentError, match="unknown_synthetic_case"):
        asyncio.run(run_agent("contest13", AdaptiveFake()))


def test_complete_empty_run_returns_zero_without_inventing_findings():
    result, _ = run("empty")
    assert (
        result.status == "completed" and result.reason == "insufficient_data" and result.cards == ()
    )


def test_mock_client_does_not_accept_real_network_transport():
    with pytest.raises(AgentError, match="mock_transport_required"):
        AdaptiveMockPlanner(httpx.AsyncHTTPTransport(), Kernel(admit("five").envelope).catalog())


def test_committed_zip_is_reproducible_synthetic_factory_output():
    from pathlib import Path

    from prototypes.adaptive_assessment.synthetic import contest_zip

    path = (
        Path(__file__).resolve().parents[4]
        / "prototypes/adaptive_assessment/fixtures/synthetic_contest13.zip"
    )
    assert path.read_bytes() == contest_zip()
