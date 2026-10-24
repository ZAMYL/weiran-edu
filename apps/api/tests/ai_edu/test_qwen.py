"""Mock HTTP only. No environment credentials or external API calls."""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.ai.edu.cards import project_card
from app.ai.edu.models import ExecutionMode
from app.ai.edu.orchestrator import run_insights
from app.ai.edu.qwen import QwenClient
from app.ai.edu.qwen_config import QwenSettings
from app.ai.edu.synthetic import CASES, build_case


def settings(**kwargs):
    return QwenSettings(
        SecretStr("synthetic-unit-test-credential"),
        "https://synthetic-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        **kwargs,
    )


def tool(name="get_dimension_statistics", **arguments):
    return {"name": name, "arguments": {"batch_id": "synthetic-batch", **arguments}}


def query(operator="<", threshold="3", **kwargs):
    return tool(
        "filter_student_group",
        conditions=[
            {"dimension": "ALGORITHMS", "operator": operator, "threshold": threshold},
        ],
        **kwargs,
    )


def native_response(calls):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"synthetic-call-{index}",
                                "type": "function",
                                "function": {
                                    "name": call["name"],
                                    "arguments": json.dumps(call["arguments"]),
                                },
                            }
                            for index, call in enumerate(calls)
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 123,
                "completion_tokens": 45,
                "total_tokens": 168,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


def selection(body, *, evidence_ids=()):
    facts = [
        fact
        for message in body["messages"]
        if message["role"] == "tool"
        for fact in json.loads(message["content"])["facts"]
    ]
    chosen = [fact["fact_id"] for fact in facts if fact["metric"] == "matched_ratio"][:1]
    if not chosen:
        chosen = [
            fact["fact_id"]
            for fact in facts
            if fact["metric"] == "mean" and fact["dimension"] == "ALGORITHMS"
        ][:1]
    return {
        "observed_facts": [{"fact_id": identifier} for identifier in chosen],
        "hypotheses": [
            {
                "kind": "practice_opportunity",
                "status": "hypothesis",
                "fact_ids": chosen,
                "evidence_ids": list(evidence_ids),
            }
        ],
        "teaching_actions": [
            {
                "kind": "guided_practice",
                "fact_ids": chosen,
                "evidence_ids": [],
            }
        ],
        "verification_steps": [
            {
                "kind": "collect_work_samples",
                "fact_ids": chosen,
                "evidence_ids": [],
            }
        ],
        "limitations": [],
        "cited_fact_ids": chosen,
        "cited_evidence_ids": list(evidence_ids),
    }


def composition_response(body, mutate=None, evidence_ids=()):
    result = selection(body, evidence_ids=evidence_ids)
    if mutate:
        mutate(result)
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(result),
                    },
                }
            ],
            "usage": {"prompt_tokens": 321, "completion_tokens": 90, "total_tokens": 411},
        },
    )


async def run_case(case_id, handler, *, client_settings=None, **kwargs):
    case, source, evidence, rubric = build_case(case_id)
    async with QwenClient(
        client_settings or settings(),
        allow_synthetic_live=True,
        transport=httpx.MockTransport(handler),
    ) as client:
        outcome = await run_insights(
            case.question,
            source,
            client,
            evidence,
            rubric,
            mode=ExecutionMode.SYNTHETIC_LIVE,
            allow_synthetic_live=True,
            **kwargs,
        )
        return outcome, client


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_all_synthetic_cases_over_mock_native_http(case):
    requests = []
    calls = {
        "overview": [tool()],
        "and_filter": [
            tool(),
            tool(
                "filter_student_group",
                include_statistics=True,
                conditions=[
                    {"dimension": "STRATEGY", "operator": ">=", "threshold": "4"},
                    {"dimension": "ALGORITHMS", "operator": "<", "threshold": "3"},
                ],
            ),
        ],
        "high_strategy_low_alg": [
            tool(),
            tool(
                "filter_student_group",
                include_statistics=True,
                conditions=[
                    {"dimension": "STRATEGY", "operator": ">=", "threshold": "4"},
                    {"dimension": "ALGORITHMS", "operator": "<", "threshold": "3"},
                ],
            ),
        ],
        "strict_lt": [query()],
        "strict_le": [query("<=")],
        "no_matches": [query(">", "5")],
        "missing": [tool()],
        "modify_scores": [],
        "cross_batch": [tool(batch_id="synthetic-other-batch")],
        "bad_evidence": [
            query(),
            tool(
                "get_evidence_records",
                evidence_ids=["synthetic-comment-2"],
            ),
        ],
        "injection": [tool()],
    }[case.case_id]

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["Authorization"] == "Bearer synthetic-unit-test-credential"
        assert body["model"] == "qwen3.8-flash" and body["enable_thinking"] is False
        if len(requests) == 1:
            assert body["max_tokens"] == 800
            assert {item["function"]["name"] for item in body["tools"]} == {
                "get_dimension_statistics",
                "filter_student_group",
                "get_evidence_records",
                "get_rubric_criteria",
            }
            return native_response(calls)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["max_tokens"] == 1800
        return composition_response(
            body,
            evidence_ids=["synthetic-comment-2"] if case.case_id == "bad_evidence" else (),
        )

    outcome, client = asyncio.run(run_case(case.case_id, handler))
    errors = {
        "modify_scores": "qwen_no_tool_calls",
        "cross_batch": "batch_not_authorized",
        "bad_evidence": "evidence_fact_scope_mismatch",
    }
    if case.case_id in errors:
        assert outcome.error_code == errors[case.case_id] and outcome.report is None
    elif case.case_id == "missing":
        assert outcome.status == "insufficient_data" and len(requests) == 1
        assert not outcome.report.hypotheses
    else:
        assert outcome.status == "validated" and outcome.report is not None
    assert len(requests) <= 2 and len(client.metrics) == len(requests)
    assert client.metrics[0].input_tokens == 123 and client.metrics[0].cached_input_tokens == 0
    assert all(item.elapsed_ms >= 0 for item in client.metrics)
    # Private locator/record IDs and evidence BODY must never enter the model payload.
    wire = json.dumps(requests, ensure_ascii=False)
    assert "synthetic-student-" not in wire and "synthetic-row-" not in wire
    assert "合成评语：" not in wire and "合成依据占位" not in wire
    card = project_card(outcome)
    assert card.execution_mode == "synthetic_live"
    if case.case_id == "strict_lt":
        assert next(item for item in outcome.facts if item.metric == "matched_count").value == 2
    if case.case_id == "strict_le":
        assert next(item for item in outcome.facts if item.metric == "matched_count").value == 3
    if case.case_id == "no_matches":
        assert card.status == "no_matches" and card.observations[0]["value_exact"] == "0"


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "qwen_bad_request"),
        (401, "qwen_authentication_failed"),
        (403, "qwen_permission_denied"),
        (404, "qwen_model_or_endpoint_unavailable"),
        (429, "qwen_rate_limited"),
        (503, "qwen_service_error"),
        (302, "qwen_redirect_denied"),
    ],
)
def test_http_errors_are_redacted_and_not_retried(status, code, caplog):
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(
            status,
            text="SYNTHETIC_PRIVATE_ERROR_BODY",
            headers={
                "Location": "https://untrusted.example/steal",
            },
        )

    outcome, client = asyncio.run(run_case("overview", handler))
    assert outcome.error_code == code and outcome.report is None and len(attempts) == 1
    assert client.metrics[0].http_status == status
    assert "PRIVATE" not in repr(outcome) and "PRIVATE" not in caplog.text


@pytest.mark.parametrize(
    "kind,code",
    [
        ("json", "qwen_invalid_response"),
        ("root_list", "qwen_invalid_response"),
        ("empty", "qwen_invalid_response"),
        ("truncated", "qwen_output_truncated"),
        ("too_large", "qwen_response_limit"),
        ("duplicate", "qwen_invalid_response"),
        ("bad_args", "qwen_invalid_response"),
        ("unknown_tool", "tool_not_allowed"),
    ],
)
def test_malformed_response_or_plan_fails_closed(kind, code):
    def handler(request):
        if kind == "json":
            return httpx.Response(200, text="bad JSON")
        if kind == "root_list":
            return httpx.Response(200, json=[])
        if kind == "empty":
            return httpx.Response(200, json={})
        if kind == "duplicate":
            return httpx.Response(200, text='{"choices":[],"choices":[]}')
        if kind == "too_large":
            return httpx.Response(200, text="x" * 65537)
        response = native_response([tool()])
        payload = json.loads(response.content)
        if kind == "truncated":
            payload["choices"][0]["finish_reason"] = "length"
        elif kind == "bad_args":
            payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
                '{"batch_id":"synthetic-batch","batch_id":"other"}'
            )
        elif kind == "unknown_tool":
            payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "execute_sql"
        return httpx.Response(200, json=payload)

    outcome, _ = asyncio.run(run_case("overview", handler))
    assert outcome.error_code == code and outcome.report is None


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda value: value["observed_facts"][0].update(value=999), "invalid_model_output"),
        (
            lambda value: value["cited_evidence_ids"].append("not-a-real-evidence"),
            "unknown_evidence_reference",
        ),
        (
            lambda value: value["observed_facts"].append({"fact_id": "invented"}),
            "unknown_fact_reference",
        ),
    ],
)
def test_real_transport_adapter_preserves_local_reference_validation(mutation, code):
    def handler(request):
        body = json.loads(request.content)
        return (
            native_response([tool()]) if "tools" in body else composition_response(body, mutation)
        )

    outcome, _ = asyncio.run(run_case("overview", handler))
    assert outcome.error_code == code and outcome.report is None


def test_json_object_still_uses_local_schema_and_reference_checks():
    def handler(request):
        body = json.loads(request.content)
        if "tools" in body:
            return native_response([tool()])
        assert body["response_format"] == {"type": "json_object"}
        assert "JSON Schema" in body["messages"][-1]["content"]
        return composition_response(body)

    outcome, _ = asyncio.run(
        run_case(
            "overview",
            handler,
            client_settings=settings(
                output_mode="json_object",
            ),
        )
    )
    assert outcome.status == "validated"


def test_timeout_and_cancellation_are_cooperative():
    async def timeout_handler(request):
        await asyncio.sleep(10)

    outcome, client = asyncio.run(
        run_case(
            "overview",
            timeout_handler,
            client_settings=settings(timeout_seconds=0.01),
        )
    )
    assert outcome.error_code == "model_timeout" and client.metrics[0].status == "model_timeout"

    async def cancel():
        started = asyncio.Event()

        async def handler(request):
            started.set()
            await asyncio.sleep(10)

        task = asyncio.create_task(run_case("overview", handler))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())


def test_no_implicit_retry_on_connection_error():
    def handler(request):
        raise httpx.ConnectError("SYNTHETIC_PRIVATE_CONNECTION", request=request)

    outcome, client = asyncio.run(run_case("overview", handler))
    assert outcome.error_code == "qwen_transport_error" and len(client.metrics) == 1


def test_subgroup_statistics_reuse_engine_and_keep_sources():
    from app.ai.edu.models import ToolCall
    from app.ai.edu.tools import ToolExecutor
    from app.analysis.edu import Dimension, summarize

    _, source, evidence, rubric = build_case("and_filter")
    executor = ToolExecutor(source.dataset, source.access, evidence, rubric)
    result = executor.execute(ToolCall(**query(include_statistics=True)))
    assert result.status == "ok"
    expected = summarize(
        source.dataset,
        batch_id="synthetic-batch",
        student_ids=["synthetic-student-0", "synthetic-student-1"],
    )
    mean = next(
        fact
        for fact in result.facts
        if fact.dimension is Dimension.ALGORITHMS and fact.metric == "mean"
    )
    assert mean.value == expected.dimension(Dimension.ALGORITHMS).mean
    assert mean.denominator == 2 and len(mean.source_refs) == 2 and mean.conditions
