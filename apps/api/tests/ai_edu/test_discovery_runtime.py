"""Synthetic mechanism tests. Fake/Mock results are not Qwen quality measurements."""

import asyncio
import json
from dataclasses import replace
from fractions import Fraction
from unittest.mock import patch

import httpx
import pytest

from app.ai.edu.discovery import AdaptiveFake, Limits, run_agent
from app.ai.edu.discovery.mock_http import MockQwenPlanner
from app.ai.edu.discovery.models import AgentError
from app.ai.edu.discovery.synthetic import build
from app.ai.edu.discovery.tools import ToolExecutor, parse_arguments
from app.analysis.edu.discovery import discover, present
from app.analysis.edu.models import Condition
from app.analysis.edu.models import Dimension as D
from app.analysis.edu.models import Operator as Op

PAIR = {"left": "ALGORITHMS", "right": "IMPLEMENTATION"}
GROUP = {
    "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": "1"}],
    "outcomes": ["IMPLEMENTATION"],
}
BOUNDARY = {
    "conditions": [
        {"dimension": "ALGORITHMS", "operator": "<=", "threshold": "2.75"},
        {"dimension": "STRATEGY", "operator": ">=", "threshold": "4"},
    ],
    "varying_dimension": "ALGORITHMS",
}


def call(name, arguments=None, **extra):
    return {"name": name, "arguments": arguments or {}, **extra}


def finish(ids=(), reason=None):
    return call(
        "finish",
        {
            "selected_fact_ids": list(ids),
            "reason": reason or ("completed" if ids else "no_valid_findings"),
        },
    )


class SequenceFake(AdaptiveFake):
    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.prompts = []

    async def decide(self, prompt):
        view = json.loads(prompt)
        self.prompts.append(view)
        selected = next(self.decisions)
        value = selected(view) if callable(selected) else selected
        return value if isinstance(value, str) else json.dumps(value)


def run(planner, case="coupled53", **kwargs):
    return asyncio.run(run_agent(case, planner, **kwargs))


def executor(case="coupled53", **kwargs):
    return ToolExecutor(build(case), "synthetic-run", Limits(**kwargs))


def execute(e, name, args=None):
    return e.execute(name, parse_arguments(name, args or {}))


def test_result_driven_paths_not_full_discover():
    with (
        patch(
            "app.analysis.edu.discovery.engine.discover",
            side_effect=AssertionError("must_not_enumerate"),
        ),
        patch(
            "app.analysis.edu.discovery.discover", side_effect=AssertionError("must_not_enumerate")
        ),
    ):
        varied = run(AdaptiveFake())
        constant = run(AdaptiveFake(), "constant17")
    assert [t.tool for t in varied.tools] == [
        "get_dimension_statistics",
        "query_dimension_relation",
        "compare_student_groups",
        "inspect_discovery_support",
    ]
    assert [t.tool for t in constant.tools] == ["get_dimension_statistics"]
    assert (varied.model_requests, varied.tool_attempts) == (5, 4)
    assert constant.reason == "no_valid_findings" and constant.cards == ()
    assert len(varied.cards) == 1
    card = varied.cards[0]
    assert card["metrics"]["target_count"] == 26
    assert card["metrics"]["remainder_count"] == 27
    assert card["chart"][0]["difference"]["numerator"] == 4
    assert card["metrics"]["student_denominator"] == 53
    assert {e.phase for e in varied.trace} >= {
        "model_selection",
        "tool_execution",
        "fact_validation",
        "final_selection",
    }
    assert all(
        any(e.phase == "fact_validation" and f.fact_id in e.fact_ids for e in varied.trace)
        for f in varied.facts
    )


@pytest.mark.parametrize("case", ["empty", "missing17", "constant17", "constant53"])
def test_no_data_and_no_findings_are_valid_stops(case):
    outcome = run(AdaptiveFake(), case)
    assert outcome.status == "completed" and not outcome.cards
    assert outcome.reason in {"no_valid_findings", "insufficient_data"}


def test_targeted_relation_only_computes_requested_pair():
    import app.analysis.edu.correlation as module

    with patch.object(module, "_pair", wraps=module._pair) as spy:
        pair, qualified = build("coupled53").relation(D.ALGORITHMS, D.IMPLEMENTATION)
    assert spy.call_count == 4  # One pair plus the three existing robustness deletions.
    assert pair.coefficient == pytest.approx(-1)
    assert qualified.ranking.robustness.passed == 3


@pytest.mark.parametrize("case", ["coupled53", "rare105", "constant17"])
def test_targeted_qualification_reuses_automatic_rules_and_ids(case):
    b = build(case)
    baseline = discover(b.dataset, batch_id=b.context.batch_id, snapshot_id=b.context.snapshot_id)
    pair, relation = b.relation(D.ALGORITHMS, D.IMPLEMENTATION)
    if relation:
        assert present(relation) == present(
            next(f for f in baseline.findings if f.discovery_id == relation.discovery_id)
        )
    else:
        assert pair.coefficient is None or abs(pair.coefficient) < 0.6
    _, rare = b.rare(D.ALGORITHMS, D.IMPLEMENTATION)
    for finding in rare:
        assert present(finding) == present(
            next(f for f in baseline.findings if f.discovery_id == finding.discovery_id)
        )


def test_group_qualification_and_actual_sources_are_not_raw_comparison():
    e = executor()
    accepted = execute(e, "compare_student_groups", GROUP)[0]
    assert accepted.discovery is not None
    assert accepted.discovery.ranking.robustness.passed == 3
    sources = {s.metric_key: s.records for s in accepted.metric_sources}
    assert len(sources["target.IMPLEMENTATION"]) == 26
    assert len(sources["remainder.IMPLEMENTATION"]) == 27
    assert set(sources["target.IMPLEMENTATION"]).isdisjoint(sources["remainder.IMPLEMENTATION"])
    # A constant outcome has a computed mean difference of zero, but no qualified card.
    e = executor()
    raw = execute(e, "compare_student_groups", {**GROUP, "outcomes": ["MODELING"]})[0]
    assert raw.discovery is None and raw.view["differences"][0]["difference"]["numerator"] == 0
    with pytest.raises(AgentError, match="unqualified_fact"):
        e.finish(parse_arguments("finish", finish([raw.fact_id])["arguments"]))


def test_relation_below_threshold_remains_investigation_only():
    e = executor("constant17")
    fact = execute(e, "query_dimension_relation", PAIR)[0]
    assert not fact.view["qualified"] and fact.view["state"] == "constant_input"
    support = execute(e, "inspect_discovery_support", {"fact_id": fact.fact_id})[0]
    assert support.view["qualification"] == "investigation_only"


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_dimension_statistics", {"batch_id": "foreign-batch"}),
        ("query_dimension_relation", {"left": "ALGORITHMS", "right": "ALGORITHMS"}),
        ("query_dimension_relation", {**PAIR, "sql": "SELECT private"}),
        ("compare_student_groups", {**GROUP, "outcomes": ["ALGORITHMS"]}),
        ("compare_student_groups", {**GROUP, "outcomes": ["MODELING", "MODELING"]}),
        (
            "compare_student_groups",
            {
                **GROUP,
                "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": "2.751"}],
            },
        ),
        (
            "compare_student_groups",
            {
                **GROUP,
                "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": 2.75}],
            },
        ),
        (
            "compare_student_groups",
            {
                **GROUP,
                "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": "5.01"}],
            },
        ),
        (
            "compare_student_groups",
            {
                **GROUP,
                "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": "NaN"}],
            },
        ),
        ("inspect_rare_combination", {**PAIR, "path": "private.xlsx"}),
    ],
)
def test_invalid_arguments_rejected_without_echo(name, args):
    fake = SequenceFake(call(name, args), finish())
    outcome = run(fake)
    assert outcome.tools[0].error == "invalid_tool_arguments"
    assert outcome.tool_attempts == 1 and outcome.status == "completed" and not outcome.facts
    assert outcome.tools[0].arguments == {}
    assert "private" not in repr(outcome.trace)


@pytest.mark.parametrize(
    "decision",
    [
        '{"name":"get_dimension_statistics","name":"finish","arguments":{}}',
        "not-json",
        call("arbitrary_sql", {"sql": "do not log secret"}),
        {**finish(), "mean": 4.75},
        call("get_dimension_statistics", basis_fact_ids=["invented"]),
    ],
)
def test_model_schema_fabrication_or_unknown_basis_is_failure(decision):
    result = run(SequenceFake(decision))
    assert result.status == "failed" and not result.cards
    assert "do not log secret" not in repr(result.trace)


def test_known_id_from_previous_run_is_not_accepted():
    old = run(AdaptiveFake())
    outcome = run(SequenceFake(finish([old.selected_ids[0]])))
    assert outcome.reason == "unknown_fact_id" and not outcome.cards


def test_unknown_final_reference_fails_closed_after_real_computation():
    outcome = run(SequenceFake(call("query_dimension_relation", PAIR), finish(["fake-id"])))
    assert len(outcome.facts) == 1 and outcome.reason == "unknown_fact_id" and not outcome.cards


def test_information_fact_cannot_be_selected_as_discovery():
    outcome = run(
        SequenceFake(call("get_dimension_statistics"), lambda v: finish([v["facts"][0]["fact_id"]]))
    )
    assert outcome.reason == "unqualified_fact" and not outcome.cards


def test_duplicate_tool_uses_semantic_arguments_and_stops():
    outcome = run(
        SequenceFake(
            call("query_dimension_relation", PAIR),
            call("query_dimension_relation", {"left": "IMPLEMENTATION", "right": "ALGORITHMS"}),
        )
    )
    assert outcome.tool_attempts == 2 and len(outcome.facts) == 1
    assert outcome.reason == "repeated_tool" and not outcome.cards


def test_equivalent_partitions_do_not_create_duplicate_cards():
    outcome = run(
        SequenceFake(
            call("compare_student_groups", GROUP),
            call(
                "compare_student_groups",
                {
                    **GROUP,
                    "conditions": [{"dimension": "ALGORITHMS", "operator": ">", "threshold": "1"}],
                },
            ),
        )
    )
    assert outcome.reason == "duplicate_discovery" and len(outcome.facts) == 1


def test_same_partition_different_analysis_semantics_are_preserved():
    e = executor()
    first = execute(e, "compare_student_groups", GROUP)[0]
    second = execute(
        e,
        "compare_student_groups",
        {
            "conditions": [{"dimension": "IMPLEMENTATION", "operator": "<=", "threshold": "1"}],
            "outcomes": ["ALGORITHMS"],
        },
    )[0]
    assert first.discovery.discovery_id != second.discovery.discovery_id
    assert second.view["differences"][0]["difference"]["numerator"] == 4


def test_tool_and_model_budgets_are_shared_and_attempts_include_errors():
    invalid = call("get_dimension_statistics", {"batch_id": "foreign"})
    outcome = run(SequenceFake(*([invalid] * 5 + [call("get_dimension_statistics")])))
    assert (outcome.model_requests, outcome.tool_attempts) == (6, 5)
    assert outcome.reason == "tool_budget_exhausted"
    outcome = run(SequenceFake(*([invalid] * 3)), limits=Limits(model_requests=3))
    assert outcome.model_requests == 3 and outcome.reason == "model_budget_exhausted"


def test_finish_after_exact_tool_budget_is_allowed():
    invalid = call("get_dimension_statistics", {"batch_id": "foreign"})
    outcome = run(SequenceFake(*([invalid] * 5 + [finish()])))
    assert outcome.status == "completed" and outcome.model_requests == 6


def test_mid_run_finish_can_decline_existing_qualified_findings():
    outcome = run(SequenceFake(call("query_dimension_relation", PAIR), finish()))
    assert outcome.facts[0].discovery is not None and outcome.cards == ()
    assert outcome.model_requests == 2


def test_timeout_does_not_return_partial_success():
    class Slow(AdaptiveFake):
        async def decide(self, prompt):
            await asyncio.sleep(0.05)
            return json.dumps(finish())

    result = run(Slow(), limits=Limits(timeout_seconds=0.001))
    assert result.reason == "model_timeout" and result.model_requests == 1 and not result.cards


def test_cancel_propagates():
    class Cancelled(AdaptiveFake):
        async def decide(self, prompt):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        run(Cancelled())


@pytest.mark.parametrize("mode", ["production", "synthetic_live"])
def test_remote_modes_closed_before_any_model_request(mode):
    fake = SequenceFake(finish())
    with pytest.raises(AgentError, match="remote_discovery_disabled"):
        run(fake, mode=mode)
    assert fake.prompts == []


def test_remote_client_cannot_be_mislabelled_by_mode():
    fake = SequenceFake(finish())
    fake.offline_only = False
    with pytest.raises(AgentError, match="remote_discovery_disabled"):
        run(fake)


def test_only_builtin_synthetic_sources_are_accepted():
    with pytest.raises(AgentError, match="unknown_synthetic_case"):
        run(AdaptiveFake(), "teacher-upload.xlsx")


def test_no_personal_rows_scatter_comments_locators_in_model_views():
    fake = SequenceFake(
        call("query_dimension_relation", PAIR), lambda v: finish([v["facts"][0]["fact_id"]])
    )
    outcome = run(fake)
    text = json.dumps(fake.prompts)
    for forbidden in (
        "synthetic-member",
        "synthetic-record",
        "synthetic-score",
        "evidence",
        "scatter",
        '"x"',
        '"y"',
        "api_key",
    ):
        # Instruction may say evidence, but payload must not include evidence fields.
        if forbidden == "evidence":
            assert '"evidence' not in text
        else:
            assert forbidden not in text
    assert all(f.metric_sources for f in outcome.facts)
    assert "synthetic-member" not in repr(outcome.facts)
    assert "controlled_queries" not in json.dumps(outcome.cards)


def test_rare_and_boundary_actual_results_and_privacy():
    rare = execute(executor("rare105"), "inspect_rare_combination", PAIR)[0]
    assert rare.discovery is not None
    assert rare.view["card"]["metrics"]["match_count"] == 5
    boundary = execute(executor("boundary30"), "verify_threshold_sensitivity", BOUNDARY)[0]
    assert (
        boundary.view["strict_count"],
        boundary.view["inclusive_count"],
        boundary.view["added_count"],
    ) == (10, 20, 10)
    with pytest.raises(AgentError, match="privacy_limited"):
        execute(executor("boundary53"), "verify_threshold_sensitivity", BOUNDARY)
    # The underlying exact 53-person oracle is still 4 versus 5; it is not sent to a model.
    raw, _ = build("boundary53").boundary(
        __import__("app.analysis.edu.discovery", fromlist=["BoundaryRequest"]).BoundaryRequest(
            (Condition(D.ALGORITHMS, Op.LE, Fraction(11, 4)), Condition(D.STRATEGY, Op.GE, 4)),
            D.ALGORITHMS,
        )
    )
    assert (len(raw.strict.matches), len(raw.inclusive.matches)) == (4, 5)


def test_output_budgets_fail_atomically():
    e = executor(output_bytes=100)
    with pytest.raises(AgentError, match="tool_output_limit"):
        execute(e, "query_dimension_relation", PAIR)
    assert not e.facts and e.output_bytes == 0


def test_source_tampering_is_not_published():
    e = executor()
    pair, qualified = e.builder.relation(D.ALGORITHMS, D.IMPLEMENTATION)
    corrupt = replace(
        qualified,
        sources=(replace(qualified.sources[0], records=qualified.sources[0].records[:-1]),),
    )
    with patch.object(e.builder, "relation", return_value=(pair, corrupt)):
        with pytest.raises(ValueError, match="source|mismatch"):
            execute(e, "query_dimension_relation", PAIR)
    assert not e.facts


def test_run_identity_is_unique_but_choices_and_values_repeat():
    first, second = run(AdaptiveFake()), run(AdaptiveFake())
    assert first.run_id != second.run_id
    assert first.cards == second.cards
    assert [(t.tool, t.status) for t in first.tools] == [(t.tool, t.status) for t in second.tools]


def native_handler(policy=None, requests=None):
    policy = policy or AdaptiveFake()

    def handler(request):
        body = json.loads(request.content)
        if requests is not None:
            requests.append(body)
        view = json.loads(body["messages"][-1]["content"])
        decision = policy.choose(view)
        arguments = {**decision["arguments"], "basis_fact_ids": decision.get("basis_fact_ids", [])}
        count = sum(m["role"] == "assistant" for m in body["messages"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"native-{count}",
                                    "type": "function",
                                    "function": {
                                        "name": decision["name"],
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            },
        )

    return handler


def test_native_http_mock_runs_both_data_driven_paths_and_reuses_one_budget():
    async def exercise(case):
        requests = []
        planner = MockQwenPlanner(httpx.MockTransport(native_handler(requests=requests)))
        try:
            result = await run_agent(case, planner)
            assert (
                planner._requests == result.model_requests == len(requests) == len(planner.metrics)
            )
            assert all(x["parallel_tool_calls"] is False for x in requests)
            assert all(x["max_tokens"] == 800 for x in requests)
            for body in requests[1:]:
                assert body["messages"][-1]["role"] == "tool"
                assert (
                    body["messages"][-1]["tool_call_id"]
                    == body["messages"][-2]["tool_calls"][0]["id"]
                )
            assert "synthetic-member" not in json.dumps(requests)
            return result
        finally:
            await planner.aclose()

    a = asyncio.run(exercise("coupled53"))
    b = asyncio.run(exercise("constant17"))
    assert a.status == b.status == "completed" and len(a.cards) == 1 and not b.cards


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_http_failures_no_retry_and_no_response_body_leak(status):
    async def exercise():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                status,
                text="secret-synthetic-untrusted-body",
                headers={"Location": "https://example.org"},
            )

        planner = MockQwenPlanner(httpx.MockTransport(handler))
        try:
            result = await run_agent("coupled53", planner)
            assert (
                result.reason == "model_request_failed" and not result.cards and len(requests) == 1
            )
            assert "secret-synthetic" not in repr(result.trace)
            assert planner.metrics[0].http_status == status
        finally:
            await planner.aclose()

    asyncio.run(exercise())


def test_http_mock_transport_mandatory():
    with pytest.raises(AgentError, match="mock_transport_required"):
        MockQwenPlanner(httpx.AsyncHTTPTransport())


@pytest.mark.parametrize(
    "variant",
    ["no_call", "multiple", "duplicate_id", "duplicate_json", "forged_number", "large", "timeout"],
)
def test_native_invalid_responses_do_not_produce_cards(variant):
    async def exercise():
        count = 0

        async def handler(request):
            nonlocal count
            count += 1
            if variant == "timeout":
                raise httpx.ReadTimeout("synthetic-private-message")
            if variant == "large":
                return httpx.Response(200, content=b"x" * 17000)
            args = '{"selected_fact_ids":[],"reason":"no_valid_findings"}'
            if variant == "duplicate_json":
                args = (
                    '{"selected_fact_ids":[],"selected_fact_ids":["fake"],'
                    '"reason":"no_valid_findings"}'
                )
            if variant == "forged_number":
                args = '{"selected_fact_ids":[],"reason":"no_valid_findings","mean":4.75}'
            native = {
                "id": "reused",
                "type": "function",
                "function": {
                    "name": "get_dimension_statistics" if variant == "duplicate_id" else "finish",
                    "arguments": "{}" if variant == "duplicate_id" else args,
                },
            }
            calls = (
                []
                if variant == "no_call"
                else [native, native]
                if variant == "multiple"
                else [native]
            )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {"role": "assistant", "tool_calls": calls},
                        }
                    ]
                },
            )

        planner = MockQwenPlanner(httpx.MockTransport(handler))
        try:
            result = await run_agent("coupled53", planner)
            assert result.status == "failed" and not result.cards
            assert "synthetic-private" not in repr(result.trace)
            assert count <= 2
        finally:
            await planner.aclose()

    asyncio.run(exercise())


def test_runtime_budget_cannot_reset_by_recreating_http_clients():
    class Recreating(AdaptiveFake):
        calls = 0

        async def decide(self, prompt):
            self.calls += 1

            class InvalidPolicy(AdaptiveFake):
                def choose(self, view):
                    return call("get_dimension_statistics", {"batch_id": "foreign"})

            client = MockQwenPlanner(httpx.MockTransport(native_handler(InvalidPolicy())))
            try:
                return await client.decide(prompt)
            finally:
                await client.aclose()

    fake = Recreating()
    result = run(fake)
    assert fake.calls == 6 and result.model_requests == 6 and result.tool_attempts == 5
    assert result.reason == "tool_budget_exhausted"


def test_unknown_support_reference_is_an_explicit_failed_tool_attempt():
    result = run(
        SequenceFake(call("inspect_discovery_support", {"fact_id": "foreign-run-f001"}), finish())
    )
    assert result.tools[0].error == "unknown_fact_id"
    assert not result.facts and not result.cards


def test_reviewed_statistics_preserve_actual_missing_sources():
    e = executor("missing17")
    fact = execute(e, "get_dimension_statistics")[0]
    algo = next(d for d in fact.view["dimensions"] if d["dimension"] == "ALGORITHMS")
    assert algo == {"dimension": "ALGORITHMS", "state": "privacy_or_data_limited"}
    assert all(s.metric_key != "stats.ALGORITHMS" for s in fact.metric_sources)
    assert all(len(s.records) == 17 for s in fact.metric_sources)


def test_small_cumulative_differences_are_withheld():
    e = executor("boundary30")
    refs = e.builder.scope.records
    e.released.append(frozenset(refs[:10]))
    with pytest.raises(AgentError, match="privacy_limited"):
        e._gate([refs[:11]])  # Both sets >=k; their one-member difference is still unsafe.


def test_unverified_model_numbers_and_stop_mismatch_are_rejected():
    result = run(SequenceFake(finish([], reason="completed")))
    assert result.reason == "inconsistent_stop_reason"
    result = run(
        SequenceFake(
            call("query_dimension_relation", PAIR),
            lambda v: finish([v["facts"][0]["fact_id"]], reason="no_valid_findings"),
        )
    )
    assert result.reason == "inconsistent_stop_reason" and not result.cards


def test_rejected_final_proposals_preserved_without_becoming_presented():
    result = run(SequenceFake(finish(["forged-id"])))
    assert result.proposed_ids == ("forged-id",)
    assert result.selected_ids == () and result.cards == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model_requests": 7},
        {"tool_attempts": 6},
        {"minimum_cell": 4},
        {"timeout_seconds": float("nan")},
        {"model_requests": True},
    ],
)
def test_invalid_runtime_limits(kwargs):
    with pytest.raises(ValueError):
        Limits(**kwargs)
