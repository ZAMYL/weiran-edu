import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from pydantic import SecretStr

from app.ai.edu.cards import project_card
from app.ai.edu.models import ExecutionMode, InsightError, Limits, SyntheticInput
from app.ai.edu.orchestrator import run_insights
from app.ai.edu.qwen import QwenClient
from app.ai.edu.qwen_config import QwenSettings
from app.ai.edu.synthetic import build_case
from app.ai.edu.tools import ToolExecutor

from .conftest import FakeLLM
from .test_qwen import composition_response, native_response, settings, tool


@pytest.mark.parametrize(
    "mode,enabled,error",
    [
        (ExecutionMode.OFFLINE, False, "offline_client_required"),
        (ExecutionMode.SYNTHETIC_LIVE, False, "synthetic_live_opt_in_required"),
        (ExecutionMode.PRODUCTION, True, "unsupported_execution_mode"),
        ("synthetic_live", True, "unsupported_execution_mode"),
    ],
)
def test_mode_boundary_blocks_before_http(mode, enabled, error):
    attempts = []

    def handler(request):
        attempts.append(request)
        raise AssertionError("no network should be attempted")

    async def run():
        case, source, evidence, rubric = build_case("overview")
        async with QwenClient(
            settings(),
            allow_synthetic_live=True,
            transport=httpx.MockTransport(handler),
        ) as client:
            return await run_insights(
                case.question,
                source,
                client,
                evidence,
                rubric,
                mode=mode,
                allow_synthetic_live=enabled,
            )

    outcome = asyncio.run(run())
    assert outcome.error_code == error and not attempts


@pytest.mark.parametrize("change", ["question", "record", "flag"])
def test_live_cannot_attest_arbitrary_data_by_setting_a_boolean(change):
    attempts = []

    async def run():
        case, source, evidence, rubric = build_case("overview")
        question = case.question
        if change == "question":
            question = "not an approved synthetic question"
        elif change == "record":
            source = SyntheticInput(
                replace(source.dataset, records=source.dataset.records[:1]),
                source.access,
            )
        else:
            source = SyntheticInput(
                source.dataset,
                source.access.model_copy(update={"synthetic_only": False}),
            )
        async with QwenClient(
            settings(),
            allow_synthetic_live=True,
            transport=httpx.MockTransport(lambda request: attempts.append(request)),
        ) as client:
            return await run_insights(
                question,
                source,
                client,
                evidence,
                rubric,
                mode=ExecutionMode.SYNTHETIC_LIVE,
                allow_synthetic_live=True,
            )

    outcome = asyncio.run(run())
    expected = "synthetic_input_required" if change == "flag" else "synthetic_scenario_required"
    assert outcome.error_code == expected and not attempts


def test_offline_fake_still_works_and_cannot_be_passed_as_live():
    async def run(mode):
        case, source, evidence, rubric = build_case("overview")
        return await run_insights(
            case.question,
            source,
            FakeLLM(),
            evidence,
            rubric,
            mode=mode,
            allow_synthetic_live=True,
        )

    assert asyncio.run(run(ExecutionMode.OFFLINE)).status == "validated"
    assert (
        asyncio.run(run(ExecutionMode.SYNTHETIC_LIVE)).error_code
        == "synthetic_live_client_required"
    )


@pytest.mark.parametrize(
    "base",
    [
        "",
        "http://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope.aliyuncs.com.attacker.example/compatible-mode/v1",
        "https://localhost/compatible-mode/v1",
        "https://user:pass@dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1?key=not-real",
        "https://dashscope.aliyuncs.com/api/v1",
        "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_no_default_region_or_untrusted_credential_destination(base):
    with pytest.raises(InsightError, match="qwen_invalid_base_url"):
        QwenSettings(SecretStr("synthetic-test-credential"), base)


def test_settings_only_read_environment_when_explicit_and_never_echo_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    with pytest.raises(InsightError, match="qwen_missing_api_key"):
        QwenSettings.from_env()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-secret-marker")
    with pytest.raises(InsightError, match="qwen_missing_base_url"):
        QwenSettings.from_env()
    monkeypatch.setenv(
        "DASHSCOPE_BASE_URL",
        "https://synthetic-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    configured = QwenSettings.from_env()
    assert configured.model == "qwen3.8-flash"
    assert "synthetic-secret-marker" not in repr(configured)
    assert "synthetic-workspace" not in repr(configured)
    with pytest.raises(InsightError, match="synthetic_live_opt_in_required"):
        QwenClient(configured)


def test_card_values_and_denominators_are_program_projection():
    from .conftest import call, scenario

    source = scenario.__wrapped__()
    outcome = source.run(FakeLLM(calls=[call()]))
    card = project_card(outcome)
    assert card.observations[0]["value_exact"] == "23/8"
    assert card.observations[0]["denominator"] == 4
    assert card.status == "needs_teacher_verification"
    assert card.observations[0]["fact_id"] in card.cited_fact_ids
    assert card.hypotheses[0].startswith("待验证假设")


def test_compatible_fact_hints_use_same_source_guard_as_validation():
    from app.ai.edu.models import ToolCall
    from app.ai.edu.prompts import composition_prompt

    from .test_qwen import query

    _, source, evidence, rubric = build_case("bad_evidence")
    executor = ToolExecutor(source.dataset, source.access, evidence, rubric)
    executor.execute(ToolCall(**query()))
    executor.execute(
        ToolCall(
            **tool(
                "get_evidence_records",
                evidence_ids=["synthetic-comment-0", "synthetic-comment-2"],
            )
        )
    )
    hints = {
        item["evidence_id"]: item
        for item in json.loads(composition_prompt(executor))["evidence_metadata"]
    }
    assert hints["synthetic-comment-0"]["compatible_fact_ids"]
    matched = {
        fact.fact_id for fact in executor.facts.values() if fact.metric.startswith("matched")
    }
    assert not matched.intersection(hints["synthetic-comment-2"]["compatible_fact_ids"])
    # The unmatched valid record may still support eligible_count, never matched facts.
    assert {
        fact.fact_id for fact in executor.facts.values() if fact.metric == "eligible_count"
    } == set(hints["synthetic-comment-2"]["compatible_fact_ids"])


def test_usage_missing_is_unknown_not_zero():
    async def run():
        count = 0

        def handler(request):
            nonlocal count
            count += 1
            body = json.loads(request.content)
            response = native_response([tool()]) if count == 1 else composition_response(body)
            payload = json.loads(response.content)
            payload.pop("usage", None)
            return httpx.Response(200, json=payload)

        case, source, evidence, rubric = build_case("overview")
        async with QwenClient(
            settings(), allow_synthetic_live=True, transport=httpx.MockTransport(handler)
        ) as client:
            outcome = await run_insights(
                case.question,
                source,
                client,
                evidence,
                rubric,
                mode=ExecutionMode.SYNTHETIC_LIVE,
                allow_synthetic_live=True,
                limits=Limits(timeout_seconds=5.0),
            )
            assert outcome.status == "validated"
            assert client.metrics[0].input_tokens is None
            assert client.metrics[1].total_tokens is None

    asyncio.run(run())


def test_no_auto_retry_after_request_budget_exhausted():
    async def budget():
        async with QwenClient(
            settings(max_requests=1),
            allow_synthetic_live=True,
            transport=httpx.MockTransport(lambda request: native_response([tool()])),
        ) as client:
            await client._post("plan", {"messages": []})
            with pytest.raises(InsightError, match="qwen_request_limit"):
                await client._post("plan", {"messages": []})
            assert len(client.metrics) == 1

    asyncio.run(budget())


@pytest.mark.parametrize(
    "code,expected",
    [
        ("qwen_authentication_failed", "technical_failure_not_evaluated"),
        ("model_timeout", "technical_failure_not_evaluated"),
        ("batch_not_authorized", "blocked_by_guard"),
        ("qwen_no_tool_calls", "no_tool_calls_safe_stop"),
    ],
)
def test_attack_evaluation_does_not_mislabel_infrastructure_errors(code, expected):
    from app.ai.edu.qwen_demo import check_intent

    assert check_intent("cross_batch", [], "error", code) == expected


def test_intent_check_uses_exact_threshold_semantics_not_spelling():
    from app.ai.edu.qwen_demo import check_intent

    assert (
        check_intent(
            "strict_lt",
            [
                {
                    "tool": "filter_student_group",
                    "arguments": {
                        "conditions": [
                            {
                                "dimension": "ALGORITHMS",
                                "operator": "<",
                                "threshold": "3.00",
                            }
                        ]
                    },
                }
            ],
            "validated",
        )
        == "passed"
    )


def test_demo_default_and_list_never_load_credentials(monkeypatch):
    from app.ai.edu import qwen_demo

    def forbidden():
        raise AssertionError("credentials should not be loaded")

    monkeypatch.setattr(qwen_demo.QwenSettings, "from_env", forbidden)
    monkeypatch.setattr("sys.argv", ["qwen_demo"])
    with pytest.raises(SystemExit) as error:
        qwen_demo.main()
    assert error.value.code == 2
    monkeypatch.setattr("sys.argv", ["qwen_demo", "--list"])
    qwen_demo.main()


def test_demo_enforces_batch_wide_api_budget_over_multiple_cases(monkeypatch):
    from app.ai.edu import qwen_demo

    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return native_response([tool()]) if "tools" in body else composition_response(body)

    monkeypatch.setattr(
        qwen_demo,
        "QwenClient",
        lambda configured, **kwargs: QwenClient(
            configured,
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )
    report = asyncio.run(qwen_demo.evaluate_live(["overview", "strict_lt"], settings(), 2))
    assert len(requests) == 2 and report["api_attempts"] == 2
    assert report["results"][1]["status"] == "not_run"
    assert report["estimated_cost"] is None


def test_card_does_not_label_mixed_group_results_as_all_empty():
    from .conftest import call, scenario

    source = scenario.__wrapped__()
    outcome = source.run(
        FakeLLM(
            calls=[
                call(
                    "filter_student_group",
                    conditions=[
                        {"dimension": "ALGORITHMS", "operator": "<", "threshold": "3"},
                    ],
                ),
                call(
                    "filter_student_group",
                    conditions=[
                        {"dimension": "ALGORITHMS", "operator": ">", "threshold": "5"},
                    ],
                ),
            ]
        )
    )
    assert outcome.status == "validated"
    assert project_card(outcome).status == "needs_teacher_verification"


def test_wire_schema_limits_ids_without_repairing_model_output():
    from app.ai.edu.models import Proposal
    from app.ai.edu.qwen import citation_schema

    original = Proposal.model_json_schema()
    view = {
        "response_schema": original,
        "facts": [{"fact_id": "fact-current"}],
        "evidence_ids": [],
    }
    narrowed = citation_schema(view)
    assert narrowed["$defs"]["ObservationChoice"]["properties"]["fact_id"]["enum"] == [
        "fact-current",
    ]
    assert narrowed["properties"]["cited_evidence_ids"]["maxItems"] == 0
    assert narrowed["properties"]["observed_facts"]["maxItems"] == 6
    assert "enum" not in original["$defs"]["ObservationChoice"]["properties"]["fact_id"]


def test_bounds_quality_check_requires_two_exact_queries():
    from app.ai.edu.qwen_demo import check_intent

    combined = [
        {"dimension": "ALGORITHMS", "operator": "<", "threshold": "3"},
        {"dimension": "ALGORITHMS", "operator": "<=", "threshold": "3"},
    ]
    assert (
        check_intent(
            "strict_bounds",
            [
                {"tool": "filter_student_group", "arguments": {"conditions": combined}},
                {"tool": "filter_student_group", "arguments": {"conditions": combined}},
            ],
            "validated",
        )
        == "failed"
    )
