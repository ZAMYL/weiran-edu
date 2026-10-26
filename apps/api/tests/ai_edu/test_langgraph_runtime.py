"""Real StateGraph vs pinned legacy loop, using identical synthetic data and Fake/HTTP Mock."""

import asyncio
import json
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.ai.edu.discovery import AdaptiveFake, Limits, run_agent
from app.ai.edu.discovery.capabilities import RegisteredCapabilities
from app.ai.edu.discovery.graph_session import RunSession
from app.ai.edu.discovery.langgraph_runtime import RunContext, _build_graph, run_agent_langgraph
from app.ai.edu.discovery.mock_http import MockQwenPlanner, adaptive_transport
from app.ai.edu.discovery.models import AgentError
from app.ai.edu.discovery.synthetic import NAMES
from app.ai.edu.discovery.tools import ToolExecutor

from .test_discovery_disclosure import make_executor
from .test_discovery_runtime import (
    BOUNDARY,
    GROUP,
    PAIR,
    SequenceFake,
    call,
    finish,
    native_handler,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from evals.discovery_langgraph.comparison import behavior  # noqa: E402


def equivalent(factory, case="coupled53", **kwargs):
    async def exercise():
        old, new = (
            await run_agent(case, factory(), **kwargs),
            await run_agent_langgraph(case, factory(), **kwargs),
        )
        assert behavior(old) == behavior(new)
        # Internal proof comparison supplements the public aggregate/source digests.
        assert [f.discovery for f in old.facts] == [f.discovery for f in new.facts]
        assert [f.metric_sources for f in old.facts] == [f.metric_sources for f in new.facts]
        return old, new

    return asyncio.run(exercise())


@pytest.mark.parametrize("case", NAMES)
def test_adaptive_fake_equivalence(case):
    with patch("app.analysis.edu.discovery.discover", side_effect=AssertionError("no_full_search")):
        _, new = equivalent(AdaptiveFake, case)
    nodes = {event.status for event in new.trace if event.phase == "graph_node"}
    assert nodes == {
        "plan",
        "validate_decision",
        "execute_tool",
        "validate_fact",
        "route",
        "finalize",
    }
    if case == "coupled53":
        card = new.cards[0]
        assert (card["metrics"]["target_count"], card["metrics"]["remainder_count"]) == (26, 27)
        assert card["chart"][0]["difference"]["numerator"] == 4
    elif case == "constant17":
        assert not new.cards and new.reason == "no_valid_findings"


@pytest.mark.parametrize("case", ["coupled53", "constant17"])
def test_real_mock_transport_equivalence(case):
    async def exercise():
        outputs = []
        histories = []
        for runner in (run_agent, run_agent_langgraph):
            planner = MockQwenPlanner(adaptive_transport())
            try:
                outputs.append(await runner(case, planner))
                histories.append(json.dumps(planner.history))
            finally:
                await planner.aclose()
        assert behavior(outputs[0]) == behavior(outputs[1])
        for run, history in zip(outputs, histories, strict=True):
            assert run.model_requests <= 6 and run.tool_attempts <= 5
            assert "synthetic-member" not in history and "synthetic-score" not in history
        assert histories[0].replace(outputs[0].run_id, "run-normalized") == histories[1].replace(
            outputs[1].run_id, "run-normalized"
        )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "decisions,limits",
    [
        ((call("get_dimension_statistics", {"batch_id": "private"}), finish()), Limits()),
        ((call("get_dimension_statistics", basis_fact_ids=["forged"]),), Limits()),
        ((finish(["forged"]),), Limits()),
        ((call("inspect_discovery_support", {"fact_id": "forged"}), finish()), Limits()),
        (
            (call("get_dimension_statistics"), lambda v: finish([v["facts"][0]["fact_id"]])),
            Limits(),
        ),
        (("not-json",), Limits()),
        (('{"name":"finish","name":"get_dimension_statistics","arguments":{}}',), Limits()),
        ((call("arbitrary_sql", {"sql": "private"}),), Limits()),
        (
            (
                call("query_dimension_relation", PAIR),
                call("query_dimension_relation", {"left": "IMPLEMENTATION", "right": "ALGORITHMS"}),
            ),
            Limits(),
        ),
        ((call("verify_threshold_sensitivity", BOUNDARY), finish()), Limits()),
        ((call("get_dimension_statistics"),), Limits(output_bytes=100)),
        ((finish(),), Limits(prompt_bytes=100)),
        (
            tuple(
                [call("get_dimension_statistics", {"bad": 1})] * 5
                + [call("get_dimension_statistics")]
            ),
            Limits(),
        ),
        (tuple([call("get_dimension_statistics", {"bad": 1})] * 5 + [finish()]), Limits()),
        (tuple([call("get_dimension_statistics", {"bad": 1})] * 3), Limits(model_requests=3)),
    ],
)
def test_errors_budgets_qualification_and_order_equivalent(decisions, limits):
    equivalent(lambda: SequenceFake(*decisions), limits=limits)


def test_twenty_person_ineligible_partition_does_not_hide_qualified_other_outcome():
    rows = [(1, Fraction(29, 10), 3, 3, 3)] * 10 + [(5, Fraction(31, 10), 3, 3, 3)] * 10

    def factory(*a, **k):
        return make_executor(rows).builder

    first = call("compare_student_groups", GROUP)
    second = call(
        "compare_student_groups",
        {
            "conditions": [{"dimension": "IMPLEMENTATION", "operator": "<=", "threshold": "2.9"}],
            "outcomes": ["ALGORITHMS"],
        },
    )

    def choose(v):
        return finish([v["facts"][1]["fact_id"]])

    with (
        patch("app.ai.edu.discovery.orchestrator.build", factory),
        patch("app.ai.edu.discovery.graph_session.build", factory),
    ):
        _, new = equivalent(lambda: SequenceFake(first, second, choose))
        assert not new.facts[0].discovery and new.facts[1].discovery
        assert new.cards[0]["chart"][0]["difference"]["numerator"] == -4
        assert new.cards[0]["metrics"]["target_count"] == 10
        reverse = call(
            "compare_student_groups",
            {
                "conditions": [
                    {"dimension": "IMPLEMENTATION", "operator": ">", "threshold": "2.9"}
                ],
                "outcomes": ["ALGORITHMS"],
            },
        )
        _, rejected = equivalent(lambda: SequenceFake(first, second, reverse))
        assert rejected.reason == "duplicate_discovery" and not rejected.cards


@pytest.mark.parametrize("delta", [1, 2, 3, 4, 5])
def test_same_round_disclosure_suppression_equivalent(delta):
    rows = [(None if i < 5 else 3, None if i < 5 + delta else 3, 3, 3, 3) for i in range(20)]

    def factory(*a, **k):
        return make_executor(rows).builder

    with (
        patch("app.ai.edu.discovery.orchestrator.build", factory),
        patch("app.ai.edu.discovery.graph_session.build", factory),
    ):
        _, new = equivalent(lambda: SequenceFake(call("get_dimension_statistics"), finish()))
    dimensions = {d["dimension"]: d for d in new.facts[0].view["dimensions"]}
    assert dimensions["ALGORITHMS"]["valid_count"] == 15
    if delta < 5:
        assert dimensions["IMPLEMENTATION"]["state"] == "privacy_or_data_limited"
    else:
        assert dimensions["IMPLEMENTATION"]["valid_count"] == 10


def test_node_reentry_and_concurrent_plan_share_budget_and_publication():
    async def exercise():
        planner = SequenceFake(call("get_dimension_statistics"), finish())
        session = RunSession("coupled53", planner, Limits())
        with patch.object(
            ToolExecutor, "execute", autospec=True, wraps=ToolExecutor.execute
        ) as spy:
            # autospec/wraps cannot dispatch the real method uniformly across Python versions.
            spy.side_effect = lambda worker, name, args: ToolExecutor._execute(worker, name, args)
            assert (
                await asyncio.gather(session.plan(0), session.plan(0)) == ["validate_decision"] * 2
            )
            for phase in (session.validate_decision, session.execute_tool):
                assert phase(0) == phase(0)
            assert spy.call_count == 1 and len(planner.prompts) == 1
            assert not session.executor.facts and session.staged[0].facts
            assert session.validate_fact(0) == "route"
            before = (
                len(session.executor.facts),
                session.executor.output_bytes,
                list(session.executor.released),
            )
            assert session.validate_fact(0) == "route"
            assert (
                len(session.executor.facts),
                session.executor.output_bytes,
                session.executor.released,
            ) == before
        assert session.route(0) == session.route(0) == "plan"
        await session.plan(1)
        session.validate_decision(1)
        session.finalize(1)
        result = session.output
        session.finalize(1)
        assert session.output is result
        assert result.model_requests == 2 and result.tool_attempts == 1

    asyncio.run(exercise())


def test_verification_failure_is_atomic_and_keeps_consumed_attempt():
    async def exercise():
        session = RunSession(
            "coupled53", SequenceFake(call("query_dimension_relation", PAIR), finish()), Limits()
        )
        await session.plan(0)
        session.validate_decision(0)
        session.execute_tool(0)
        assert not session.executor.facts and session.staged[1]
        worker, facts = session.staged
        contaminated = replace(facts[0], view={**facts[0].view, "raw_rows": "private-canary"})
        worker.facts[contaminated.fact_id] = contaminated
        session.staged = worker, (contaminated,)
        session.validate_fact(0)
        assert not session.executor.facts and not session.executor.discoveries
        assert session.executor.output_bytes == 0 and len(session.executor.released) == 1
        assert session.tool_attempts == 1 and len(session.executor.seen) == 1
        assert session.tools[0].error == "unsupported_model_projection"
        assert "private-canary" not in repr(session.trace)

    asyncio.run(exercise())


@pytest.mark.parametrize("runner", [run_agent, run_agent_langgraph])
def test_timeout_and_cancellation(runner):
    class Slow(AdaptiveFake):
        async def decide(self, prompt):
            await asyncio.sleep(0.05)
            return json.dumps(finish())

    class Cancelled(AdaptiveFake):
        async def decide(self, prompt):
            raise asyncio.CancelledError

    result = asyncio.run(runner("coupled53", Slow(), limits=Limits(timeout_seconds=0.001)))
    assert result.reason == "model_timeout" and not result.cards and result.model_requests == 1
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner("coupled53", Cancelled()))


def test_graph_is_six_real_nodes_and_persists_no_input():
    graph = _build_graph()
    assert graph.checkpointer is None and graph.store is None
    assert set(graph.nodes) == {
        "__start__",
        "plan",
        "validate_decision",
        "execute_tool",
        "validate_fact",
        "route",
        "finalize",
    }
    assert set(graph.get_input_jsonschema()["properties"]) == {"turn", "destination"}

    async def exercise():
        session = RunSession("constant17", AdaptiveFake(), Limits())
        async for state in graph.astream(
            {"turn": 0, "destination": "plan"},
            context=RunContext(session),
            config={"callbacks": [], "recursion_limit": 64},
            stream_mode="values",
        ):
            assert set(state) == {"turn", "destination"} and isinstance(state["turn"], int)

    asyncio.run(exercise())


def test_cloud_tracing_disabled_even_with_environment_opt_in(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with patch(
        "langsmith.client.Client.create_run", side_effect=AssertionError("cloud_disabled")
    ) as cloud:
        result = asyncio.run(run_agent_langgraph("constant17", AdaptiveFake()))
        cloud.assert_not_called()
    assert result.status == "completed"


def test_registry_restricts_available_tools_without_new_assessment_adapter():
    registry = RegisteredCapabilities(tools=frozenset())
    result = asyncio.run(
        run_agent_langgraph(
            "coupled53",
            SequenceFake(call("get_dimension_statistics"), finish()),
            capabilities=registry,
        )
    )
    assert result.tools[0].error == "tool_not_available" and not result.facts
    with pytest.raises(AgentError):
        RegisteredCapabilities(tools=frozenset({"arbitrary_sql"}))


@pytest.mark.parametrize("mode", ["production", "synthetic_live"])
def test_remote_entry_remains_closed(mode):
    with pytest.raises(AgentError, match="remote_discovery_disabled"):
        asyncio.run(run_agent_langgraph("coupled53", AdaptiveFake(), mode=mode))


@pytest.mark.parametrize("delta", [1, 2, 3, 4])
@pytest.mark.parametrize("multiple", [False, True])
def test_group_disclosure_failure_publishes_nothing_in_both_runners(delta, multiple):
    if multiple:
        rows = [
            (
                1 if i < 20 else 5,
                None if i < 5 else (5 if i < 20 else 1),
                None if i < 5 + delta else (5 if i < 20 else 1),
                3,
                3,
            )
            for i in range(40)
        ]
        arguments = {**GROUP, "outcomes": ["IMPLEMENTATION", "MODELING"]}
    else:
        rows = [
            (1 if i < 10 else 5, None if i < delta else (5 if i < 10 else 1), 3, 3, 3)
            for i in range(20)
        ]
        arguments = GROUP

    def factory(*args, **kwargs):
        return make_executor(rows).builder

    with (
        patch("app.ai.edu.discovery.orchestrator.build", factory),
        patch("app.ai.edu.discovery.graph_session.build", factory),
    ):
        _, new = equivalent(
            lambda: SequenceFake(call("compare_student_groups", arguments), finish())
        )
    assert new.tools[0].error == "privacy_limited" and not new.facts and not new.cards


def test_recreated_graph_cannot_reset_session_or_repeat_work():
    async def exercise():
        planner = SequenceFake(call("get_dimension_statistics"), finish())
        session = RunSession("coupled53", planner, Limits())
        for _ in range(2):
            await _build_graph().ainvoke(
                {"turn": 0, "destination": "plan"},
                context=RunContext(session),
                config={"callbacks": [], "recursion_limit": 64},
            )
        assert (
            len(planner.prompts) == 2 and session.model_requests == 2 and session.tool_attempts == 1
        )
        assert len(session.executor.facts) == 1 and len(session.tools) == 1
        with pytest.raises(AgentError, match="invalid_graph_transition"):
            session.execute_tool(99)

    asyncio.run(exercise())


def test_staging_cannot_mutate_previously_published_facts_or_publish_a_different_object():
    async def exercise():
        planner = SequenceFake(
            call("get_dimension_statistics"), call("query_dimension_relation", PAIR)
        )
        session = RunSession("coupled53", planner, Limits())
        await session.plan(0)
        session.validate_decision(0)
        session.execute_tool(0)
        session.validate_fact(0)
        original = next(iter(session.executor.facts.values()))
        original_view = json.dumps(original.view, sort_keys=True)
        session.route(0)
        await session.plan(1)
        session.validate_decision(1)
        session.execute_tool(1)
        worker, _ = session.staged
        worker.facts[original.fact_id].view["private"] = "private-canary"
        session.validate_fact(1)
        assert json.dumps(original.view, sort_keys=True) == original_view
        assert list(session.executor.facts) == [original.fact_id]
        assert session.tools[-1].error == "source_validation_failed" and session.tool_attempts == 2

    asyncio.run(exercise())


def test_run_deadline_is_checked_before_a_model_request():
    with patch("app.ai.edu.discovery.orchestrator.perf_counter", side_effect=[0, 100, 100]):
        old = asyncio.run(run_agent("constant17", AdaptiveFake()))
    with patch("app.ai.edu.discovery.graph_session.perf_counter", side_effect=[0, 100, 100]):
        new = asyncio.run(run_agent_langgraph("constant17", AdaptiveFake()))
    assert behavior(old) == behavior(new)
    assert new.reason == "run_timeout" and new.model_requests == 0


def test_cancellation_between_execution_and_verification_cannot_publish():
    async def exercise():
        session = RunSession(
            "coupled53", SequenceFake(call("query_dimension_relation", PAIR)), Limits()
        )
        await session.plan(0)
        session.validate_decision(0)
        session.execute_tool(0)
        session.cancelled = True
        with pytest.raises(asyncio.CancelledError):
            session.validate_fact(0)
        assert not session.executor.facts and session.tool_attempts == 1
        assert len(session.executor.seen) == 1

    asyncio.run(exercise())


def test_inherited_cloud_tracer_is_suppressed_and_restored(monkeypatch):
    from langchain_core.tracers.context import tracing_v2_callback_var

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    sentinel = object()
    token = tracing_v2_callback_var.set(sentinel)
    try:
        with patch(
            "langsmith.client.Client.create_run", side_effect=AssertionError("cloud_disabled")
        ) as cloud:
            result = asyncio.run(run_agent_langgraph("constant17", AdaptiveFake()))
            cloud.assert_not_called()
        assert result.status == "completed" and tracing_v2_callback_var.get() is sentinel
    finally:
        tracing_v2_callback_var.reset(token)


def test_registry_checks_referenced_assessment_dimensions_before_computing():
    registry = RegisteredCapabilities(dimensions=frozenset({"ALGORITHMS"}))
    result = asyncio.run(
        run_agent_langgraph(
            "coupled53",
            SequenceFake(call("query_dimension_relation", PAIR), finish()),
            capabilities=registry,
        )
    )
    assert result.tools[0].error == "tool_not_available" and not result.facts


@pytest.mark.parametrize("failure", ["projection", "source"])
def test_shared_kernel_faults_keep_both_runners_atomic(failure):
    target = "model_discovery" if failure == "projection" else "ToolExecutor._validate_discovery"
    with patch(
        "app.ai.edu.discovery.tools." + target, side_effect=AgentError("source_scope_mismatch")
    ):
        _, new = equivalent(lambda: SequenceFake(call("query_dimension_relation", PAIR)))
    assert new.reason == "source_scope_mismatch" and not new.facts and not new.cards


def test_recreating_native_clients_does_not_refresh_graph_budget():
    class InvalidPolicy(AdaptiveFake):
        def choose(self, view):
            return call("get_dimension_statistics", {"batch_id": "private"})

    class Recreating(AdaptiveFake):
        async def decide(self, prompt):
            planner = MockQwenPlanner(httpx.MockTransport(native_handler(InvalidPolicy())))
            try:
                return await planner.decide(prompt)
            finally:
                await planner.aclose()

    _, new = equivalent(Recreating)
    assert new.model_requests == 6 and new.tool_attempts == 5
    assert new.reason == "tool_budget_exhausted"


@pytest.mark.parametrize(
    "variant",
    ["empty", "multiple", "duplicate_json", "unknown_tool", "duplicate_id", "large", "timeout"],
)
def test_native_protocol_failures_equivalent(variant):
    async def exercise():
        results = []
        for runner in (run_agent, run_agent_langgraph):

            def handler(request):
                if variant == "timeout":
                    raise httpx.ReadTimeout("private-canary")
                if variant == "large":
                    return httpx.Response(200, content=b"x" * 17000)
                native = {
                    "id": "duplicate-id",
                    "type": "function",
                    "function": {
                        "name": "arbitrary_sql"
                        if variant == "unknown_tool"
                        else "get_dimension_statistics"
                        if variant == "duplicate_id"
                        else "finish",
                        "arguments": (
                            '{"reason":"no_valid_findings","reason":"completed",'
                            '"selected_fact_ids":[]}'
                        )
                        if variant == "duplicate_json"
                        else "{}"
                        if variant == "duplicate_id"
                        else '{"selected_fact_ids":[],"reason":"no_valid_findings"}',
                    },
                }
                calls = (
                    []
                    if variant == "empty"
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
                results.append(await runner("coupled53", planner))
            finally:
                await planner.aclose()
        assert behavior(results[0]) == behavior(results[1])
        assert results[1].status == "failed" and not results[1].cards
        assert "private-canary" not in repr(results[1].trace)

    asyncio.run(exercise())


def test_existing_other_run_fact_id_cannot_be_selected():
    previous = asyncio.run(run_agent("coupled53", AdaptiveFake()))
    _, result = equivalent(lambda: SequenceFake(finish([previous.selected_ids[0]])))
    assert result.reason == "unknown_fact_id" and not result.cards
    assert result.proposed_ids == previous.selected_ids


def test_synthetic_context_bindings_match_without_changing_scope():
    context = tuple(
        prefix + "-" + "a" * 20
        for prefix in ("synthetic-batch", "synthetic-snapshot", "synthetic-source")
    )
    _, result = equivalent(AdaptiveFake, synthetic_context=context)
    assert result.cards[0]["batch_id"] == context[0]
    assert result.cards[0]["snapshot_id"] == context[1]


def test_finalization_cannot_skip_fact_verification():
    async def exercise():
        session = RunSession(
            "coupled53", SequenceFake(call("query_dimension_relation", PAIR)), Limits()
        )
        await session.plan(0)
        session.validate_decision(0)
        session.execute_tool(0)
        with pytest.raises(AgentError, match="invalid_graph_transition"):
            session.finalize(0)
        assert not session.executor.facts and session.output is None

    asyncio.run(exercise())


@pytest.mark.parametrize("runner", [run_agent, run_agent_langgraph])
def test_caller_task_cancellation_is_not_a_successful_graph_result(runner):
    async def exercise():
        entered = asyncio.Event()
        calls = 0

        class Waiting(AdaptiveFake):
            async def decide(self, prompt):
                nonlocal calls
                calls += 1
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(runner("coupled53", Waiting()))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("case", ["coupled53", "constant17"])
def test_archived_trace_replays_current_fake_behavior_and_source_bindings(case):
    from scripts.edu_langgraph_demo import trace_projection

    root = Path(__file__).resolve().parents[4]
    archive = json.loads(
        (root / "evals/discovery_langgraph/offline-trace.json").read_text(encoding="utf-8")
    )
    record = next(
        c for c in archive["comparisons"] if c["case"] == case and c["transport"] == "AdaptiveFake"
    )

    def normalize(payload):
        payload = dict(payload)
        identity = payload.pop("run_id")
        for key in ("elapsed_seconds", "tool_compute_seconds", "non_tool_seconds"):
            assert payload.pop(key) >= 0
        return json.loads(json.dumps(payload).replace(identity, "run-normalized"))

    for name, runner in (("baseline", run_agent), ("langgraph", run_agent_langgraph)):
        actual = asyncio.run(runner(case, AdaptiveFake()))
        assert normalize(trace_projection(actual)) == normalize(record[name])
        assert [fact["source_digest"] for fact in behavior(actual)["facts"]] == record[
            "source_digests"
        ]
    assert archive["equivalent"] and archive["checkpointer"] is None
    rendered = json.dumps(archive)
    assert not any(
        private in rendered
        for private in (
            "synthetic-member",
            "synthetic-score",
            '"student_id"',
            '"raw_rows"',
            '"evidence_refs"',
            "synthetic-mock-placeholder",
        )
    )


def test_trace_cli_never_overwrites_an_existing_file(tmp_path, capsys):
    from scripts.edu_langgraph_demo import main

    path = tmp_path / "existing.json"
    path.write_text("retain", encoding="utf-8")
    assert main(["--output", str(path)]) == 2
    assert path.read_text() == "retain"
    assert "NEW_JSON_OUTPUT_REQUIRED" in capsys.readouterr().out
