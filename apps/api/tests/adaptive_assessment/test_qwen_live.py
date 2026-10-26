"""Protocol, budget and privacy regression tests. No network or environment credentials."""

import asyncio
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from prototypes.adaptive_assessment.agent import AdaptiveExecutor, run_adaptive
from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.cli import main, preflight
from prototypes.adaptive_assessment.live.demo import (
    MOCK_BASE,
    MOCK_DATE,
    mock_authorization,
    protocol_mock,
)
from prototypes.adaptive_assessment.live.planner import QwenAdaptivePlanner, run_synthetic_live
from prototypes.adaptive_assessment.live.pricing import PRICE
from prototypes.adaptive_assessment.synthetic import admit
from pydantic import SecretStr, ValidationError

from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.discovery.session import run_session
from app.ai.edu.qwen_config import QwenSettings


def budget_for(tmp_path, recipe="five", **overrides):
    return CampaignBudget(
        mock_authorization(recipe, **overrides), tmp_path, origin="http_mock", today=MOCK_DATE
    )


def run(recipe, budget, **kwargs):
    try:
        return asyncio.run(run_synthetic_live(recipe, budget, **kwargs))
    finally:
        budget.close()


@pytest.mark.parametrize(
    "recipe,expected",
    [
        (
            "five",
            [
                "get_dimension_statistics",
                "query_dimension_relation",
                "compare_student_groups",
                "inspect_discovery_support",
                "finish",
            ],
        ),
        (
            "contest13",
            [
                "get_dimension_statistics",
                "inspect_item_quality",
                "compare_student_groups",
                "inspect_discovery_support",
                "finish",
            ],
        ),
        (
            "contest13_complete",
            [
                "get_dimension_statistics",
                "query_dimension_relation",
                "compare_student_groups",
                "inspect_discovery_support",
                "finish",
            ],
        ),
        ("small", ["get_dimension_statistics", "finish"]),
    ],
)
def test_native_multiround_uses_actual_results(tmp_path, recipe, expected):
    captured = []
    result = run(recipe, budget_for(tmp_path, recipe), transport=protocol_mock(capture=captured))
    assert result["status"] == "completed", result
    assert [c["tool"] for c in result["model_choices"]] == expected
    assert result["model_requests"] == len(expected) <= 6
    assert result["tool_attempts"] == len(expected) - 1 <= 5
    assert result["measurement_origin"] == "offline_mechanism"
    assert result["budget"]["request_count"] == len(expected)
    assert result["budget"]["charged_tokens"] == sum(1100 + 100 * i for i in range(len(expected)))
    assert all(e["state"] == "settled" for e in result["budget"]["entries"])
    for i, body in enumerate(captured):
        assert body["model"] == "qwen3.8-flash"
        assert body["max_tokens"] == 800 and body["parallel_tool_calls"] is False
        assert body["enable_thinking"] is False
        assert len(body["messages"]) == 2 + 2 * i
        if i:
            assert body["messages"][-1]["role"] == "tool"
            assert (
                body["messages"][-1]["tool_call_id"] == body["messages"][-2]["tool_calls"][0]["id"]
            )
            assert json.loads(body["messages"][-1]["content"])["facts"]
        raw = json.dumps(body, ensure_ascii=False)
        for private in (
            "source_queries",
            "evidence_id",
            "reference_id",
            "participants",
            "source_sha256",
            "S0001",
            "answers/",
            "case_id",
            "oracle",
        ):
            assert private not in raw
        ids = json.loads(body["messages"][-1]["content"])["analysis_context"]["items"]
        assert len(ids) == (5 if recipe == "five" else 13)
        assert {i["item_id"] for i in ids} == (
            {f"I{i:02}" for i in range(1, 6)}
            if recipe == "five"
            else {f"Q{i:02}" for i in range(1, 14)}
        )
    if recipe == "five":
        group = next(c for c in result["cards"] if c["type"] == "item_group_difference")
        assert group["metrics"]["target_count"] == 26
        assert group["metrics"]["remainder_count"] == 27
        assert abs(group["metrics"]["difference"]["numerator"]) == 4
    if recipe == "small":
        assert result["cards"] == ()


@pytest.mark.parametrize("recipe", ["limited", "no_evidence", "empty"])
def test_partial_data_never_fabricates_scale_or_evidence(tmp_path, recipe):
    result = run(recipe, budget_for(tmp_path, recipe), transport=protocol_mock())
    assert result["model_requests"] <= 6
    assert result["tool_attempts"] <= 5
    assert result["budget"]["request_count"] == result["model_requests"]
    if recipe == "empty":
        assert not result["cards"]
    if recipe == "no_evidence":
        # Q02 alone has absent evidence in this fixture; Q01/Q03 remain usable.
        assert all(c["metrics"].get("outcome") != "Q02" for c in result["cards"])
    if recipe == "limited":
        assert all(
            "Q12" not in c.get("arguments", {}).get("outcomes", []) for c in result["model_choices"]
        )


@pytest.mark.parametrize(
    "change",
    [
        "unknown_tool",
        "unknown_item",
        "unknown_fact",
        "bad_json",
        "duplicate_id",
        "repeated_tool",
        "wrong_model",
    ],
)
def test_bad_native_responses_fail_closed(tmp_path, change):
    def mutate(body, index, view):
        function = body["choices"][0]["message"]["tool_calls"][0]["function"]
        if change == "unknown_tool":
            function["name"] = "run_sql"
        elif change == "unknown_item":
            function.update(name="inspect_item_quality", arguments='{"item_id":"Q99"}')
        elif change == "unknown_fact":
            function.update(
                name="finish", arguments='{"selected_fact_ids":["invented"],"reason":"completed"}'
            )
        elif change == "bad_json":
            function["arguments"] = '{"item_ids":[],"item_ids":null}'
        elif change == "duplicate_id" and index:
            body["choices"][0]["message"]["tool_calls"][0]["id"] = "live-mock-0"
        elif change == "repeated_tool":
            function.update(name="get_dimension_statistics", arguments="{}")
        elif change == "wrong_model":
            body["model"] = "other-billing-model"
        return body

    result = run("five", budget_for(tmp_path), transport=protocol_mock(mutate=mutate))
    assert result["status"] == "failed"
    assert not result["cards"]
    assert result["model_requests"] <= 2


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 999},
        {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
        {"prompt_tokens": 991809, "completion_tokens": 1, "total_tokens": 991810},
        {"prompt_tokens": 1, "completion_tokens": 801, "total_tokens": 802},
    ],
)
def test_unverifiable_usage_holds_reservation_and_stops(tmp_path, usage):
    def mutate(body, *_):
        body["usage"] = usage
        return body

    result = run("five", budget_for(tmp_path), transport=protocol_mock(mutate=mutate))
    assert result["reason"] == "unverifiable_usage"
    assert not result["cards"] and result["budget"]["request_count"] == 1
    assert result["budget"]["entries"][0]["state"] == "usage_unknown_reservation_held"
    assert result["budget"]["charged_tokens"] == PRICE.max_input_tokens + 800


@pytest.mark.parametrize(
    "limit,expected",
    [
        ({"max_requests": 1}, "authorized_request_limit"),
        ({"total_token_limit": 2000}, "authorized_token_limit"),
        ({"cost_cap_cny": "0.001"}, "authorized_cost_limit"),
    ],
)
def test_request_token_cost_limits_before_network(tmp_path, limit, expected):
    capture = []
    b = budget_for(tmp_path, **limit)
    # Session requests also stop at the authorization's lower cap.
    result = run("five", b, transport=protocol_mock(capture=capture))
    assert result["status"] == "failed" and not result["cards"]
    if "max_requests" in limit:
        assert len(capture) == 1
        assert result["reason"] == "model_budget_exhausted"
    else:
        assert not capture
        assert result["reason"] == expected


def test_reserve_and_settle_independent_decimal_cost(tmp_path):
    result = run("small", budget_for(tmp_path, "small"), transport=protocol_mock())
    # 900*0.8 + 100*0.1 + 100*2.7; then 1000*0.8 + 100*0.1 + 100*2.7.
    assert Decimal(result["budget"]["charged_cny_upper_bound"]) == Decimal("0.00208")
    assert Decimal(result["budget"]["entries"][0]["reserved_cny"]) == Decimal("0.7956064")


def test_campaign_cannot_reset_by_client_or_process_restart(tmp_path):
    b = budget_for(tmp_path)
    e = AdaptiveExecutor(admit("five"), "test-run", Limits())
    p = QwenAdaptivePlanner(e, b, transport=protocol_mock())
    with pytest.raises(AgentError, match="campaign_run_already_claimed"):
        QwenAdaptivePlanner(e, b, transport=protocol_mock())
    b.close()
    asyncio.run(p.aclose())
    with pytest.raises(AgentError, match="campaign_already_consumed"):
        budget_for(tmp_path)


@pytest.mark.parametrize("exception", ["timeout", "cancel"])
def test_timeout_and_cancellation_never_refund_unknown_charge(tmp_path, exception):
    async def exercise():
        seen = asyncio.Event()

        async def handler(request):
            seen.set()
            await asyncio.sleep(5)
            raise AssertionError("must be cancelled")

        b = budget_for(tmp_path)
        settings = QwenSettings(
            SecretStr("synthetic-mock-placeholder"), MOCK_BASE, timeout_seconds=0.02
        )
        task = asyncio.create_task(
            run_synthetic_live("five", b, transport=httpx.MockTransport(handler), settings=settings)
        )
        try:
            await seen.wait()
            if exception == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result["reason"] == "model_timeout" and not result["cards"]
            assert b.snapshot()["request_count"] == 1
            assert b.snapshot()["blocked"] == "unverifiable_usage"
            with pytest.raises(AgentError, match="unverifiable_usage"):
                b.reserve()
        finally:
            b.close()

    asyncio.run(exercise())


def test_offline_entry_and_mock_network_identity_remain_closed(tmp_path):
    b = budget_for(tmp_path)
    e = AdaptiveExecutor(admit("five"), "test-run", Limits())
    p = QwenAdaptivePlanner(e, b, transport=protocol_mock())
    try:
        with pytest.raises(AgentError, match="remote_discovery_disabled"):
            asyncio.run(run_adaptive("five", p))
        with pytest.raises(AgentError, match="remote_discovery_disabled"):
            asyncio.run(run_session(e, p, limits=Limits()))
    finally:
        asyncio.run(p.aclose())
        b.close()
    b2 = budget_for(tmp_path / "b")
    with pytest.raises(AgentError, match="mock_transport_required"):
        QwenAdaptivePlanner(e, b2, allow_network=True)
    b2.close()


def test_fingerprint_region_price_and_expiry_guards(tmp_path):
    with pytest.raises((ValidationError, ValueError)):
        mock_authorization("five", source_fingerprints={"five": "0" * 64})
    with pytest.raises(AgentError, match="stale_authorization_or_price"):
        budget_for(tmp_path, price_fingerprint="0" * 64)
    with pytest.raises(AgentError, match="unverified_model_region_or_stale_price"):
        PRICE.check(
            PRICE.model, "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", MOCK_DATE
        )
    with pytest.raises(AgentError):
        PRICE.check(PRICE.model, MOCK_BASE, date(2026, 11, 3))
    b = budget_for(tmp_path / "fresh")
    with pytest.raises(AgentError, match="authorization_scope_mismatch"):
        run("contest13", b, transport=protocol_mock())


def test_dynamic_schema_uses_actual_items_and_scales_without_oracles():
    five, contest, limited = [preflight(r) for r in ("five", "contest13", "limited")]
    assert len(five["capabilities"]["items"]) == 5
    assert len(contest["capabilities"]["items"]) == 13
    raw = json.dumps(contest)
    assert "I01" not in raw
    assert all(item["scale"]["maximum"] == "100" for item in contest["capabilities"]["items"])
    relation = next(
        t for t in limited["tools"] if t["function"]["name"] == "query_dimension_relation"
    )
    assert "Q12" not in relation["function"]["parameters"]["properties"]["left"]["enum"]


def test_default_cli_has_no_client_credential_read_or_network(monkeypatch, capsys):
    def prohibited(*args, **kwargs):
        raise AssertionError("dry-run must not read credentials or create HTTP clients")

    monkeypatch.setattr(QwenSettings, "from_env", prohibited)
    monkeypatch.setattr(httpx, "AsyncClient", prohibited)
    assert main(["--recipe", "contest13"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run_no_network"
    with pytest.raises(SystemExit):
        main(["--execute"])


def test_constant_seventeen_stops_with_no_findings(tmp_path):
    result = run("constant17", budget_for(tmp_path, "constant17"), transport=protocol_mock())
    assert result["status"] == "completed" and result["reason"] == "no_valid_findings"
    assert not result["cards"]
    assert [c["tool"] for c in result["model_choices"]] == ["get_dimension_statistics", "finish"]


def test_both_synthetic_structures_same_campaign_scope_is_not_extendable(tmp_path):
    auth = mock_authorization("five")
    b = CampaignBudget(auth, tmp_path, origin="http_mock", today=MOCK_DATE)
    # Caller mutation of the original dict cannot alter a constructed campaign.
    auth.source_fingerprints["contest13"] = admit("contest13").fingerprint
    e = AdaptiveExecutor(admit("contest13"), "test-run", Limits())
    with pytest.raises(AgentError, match="authorization_scope_mismatch"):
        QwenAdaptivePlanner(e, b, transport=protocol_mock())
    b.authorization.source_fingerprints["contest13"] = admit("contest13").fingerprint
    with pytest.raises(AgentError, match="authorization_changed"):
        QwenAdaptivePlanner(e, b, transport=protocol_mock())
    b.close()


def test_six_requests_and_five_tool_attempts_are_actual_hard_limits(tmp_path):
    class BudgetPolicy:
        def choose(self, view):
            # Distinct valid tools, no data-driven discovery claim in this adversarial test.
            n = 5 - view["remaining_tool_attempts"]
            return (
                {"name": "get_dimension_statistics", "arguments": {"item_ids": [f"I{n + 1:02}"]}}
                if n < 5
                else {"name": "inspect_item_quality", "arguments": {"item_id": "I01"}}
            )

    result = run(
        "constant17", budget_for(tmp_path, "constant17"), transport=protocol_mock(BudgetPolicy())
    )
    assert result["model_requests"] == result["budget"]["request_count"] == 6
    assert result["tool_attempts"] == 5
    assert result["reason"] == "tool_budget_exhausted" and not result["cards"]


def test_budget_cannot_be_ignored_by_direct_client_calls(tmp_path):
    from app.ai.edu.qwen_config import QwenSettings

    b = budget_for(tmp_path, max_requests=1)
    b.claim(QwenSettings(SecretStr("synthetic-placeholder"), MOCK_BASE), admit("five"))
    ticket = b.reserve()
    from app.ai.edu.qwen import CallMetric

    metric = CallMetric(
        "adaptive_discovery", PRICE.model, 1, "response_received", 200, 100, 100, 200, 0, 800
    )
    b.settle(ticket, metric, failed=False)
    with pytest.raises(AgentError, match="authorized_request_limit"):
        b.reserve()
    b.close()


def test_journal_failure_before_http_is_not_a_free_retry(tmp_path, monkeypatch):
    b = budget_for(tmp_path)
    e = AdaptiveExecutor(admit("five"), "test-run", Limits())
    p = QwenAdaptivePlanner(e, b, transport=protocol_mock())

    def fail(*args):
        raise OSError("synthetic-error-no-sensitive-data")

    monkeypatch.setattr("os.fsync", fail)
    with pytest.raises(AgentError, match="budget_journal_failed"):
        b.reserve()
    assert b.snapshot()["request_count"] == 1
    assert b.snapshot()["blocked"] == "budget_journal_failed"
    assert p._http is None
    b.close()


def test_midrun_missing_usage_does_not_return_earlier_valid_facts_as_cards(tmp_path):
    def mutate(body, index, *_):
        if index == 2:
            body.pop("usage")
        return body

    result = run("five", budget_for(tmp_path), transport=protocol_mock(mutate=mutate))
    assert len(result["verified_facts"]) == 2
    assert result["reason"] == "unverifiable_usage" and not result["cards"]
    assert result["budget"]["entries"][2]["state"] == "usage_unknown_reservation_held"


def test_real_network_requires_explicit_optin_and_environment_only_settings(tmp_path, monkeypatch):
    from prototypes.adaptive_assessment.live import budget as module

    monkeypatch.setattr(
        module,
        "datetime",
        type(
            "Clock",
            (),
            {"now": staticmethod(lambda _: type("D", (), {"date": lambda _: MOCK_DATE})())},
        ),
    )
    b = CampaignBudget(mock_authorization("five"), tmp_path, origin="synthetic_live")
    e = AdaptiveExecutor(admit("five"), "test-run", Limits())
    for kwargs in (
        {},
        {"allow_network": True, "transport": protocol_mock()},
        {"allow_network": True, "settings": object()},
    ):
        with pytest.raises(AgentError, match="synthetic_live_opt_in_required"):
            QwenAdaptivePlanner(e, b, **kwargs)
    assert b.snapshot()["request_count"] == 0
    b.close()


def test_unsafe_caller_prompt_cannot_be_forwarded(tmp_path):
    b = budget_for(tmp_path)
    e = AdaptiveExecutor(admit("five"), "test-run", Limits())
    p = QwenAdaptivePlanner(e, b, transport=protocol_mock())
    with pytest.raises(AgentError, match="unsafe_planner_view"):
        asyncio.run(p.decide('{"raw_student_rows":[1,2,3]}'))
    assert b.snapshot()["request_count"] == 0
    b.close()


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_http_failures_have_no_retry_or_error_body_leak(tmp_path, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="synthetic-sensitive-error-body")

    result = run("five", budget_for(tmp_path), transport=httpx.MockTransport(handler))
    assert result["status"] == "failed" and len(calls) == 1 and not result["cards"]
    assert "synthetic-sensitive-error-body" not in json.dumps(result)
    assert result["budget"]["blocked"] == "unverifiable_usage"
    assert result["budget"]["entries"][0]["http_status"] == status


@pytest.mark.parametrize(
    "limits,reason",
    [
        ({"total_token_limit": 993000}, "authorized_token_limit"),
        ({"cost_cap_cny": "0.796"}, "authorized_cost_limit"),
    ],
)
def test_remaining_budget_is_checked_again_before_every_request(tmp_path, limits, reason):
    capture = []
    result = run("five", budget_for(tmp_path, **limits), transport=protocol_mock(capture=capture))
    assert len(capture) == 1
    assert result["reason"] == reason and not result["cards"]
    assert result["budget"]["charged_tokens"] == 1100


def test_model_cannot_select_unqualified_summary_as_discovery(tmp_path):
    class BadFinish:
        def choose(self, view):
            if not view["facts"]:
                return {"name": "get_dimension_statistics", "arguments": {}}
            return {
                "name": "finish",
                "arguments": {
                    "selected_fact_ids": [view["facts"][0]["fact_id"]],
                    "reason": "completed",
                },
            }

    result = run("five", budget_for(tmp_path), transport=protocol_mock(BadFinish()))
    assert result["status"] == "failed" and not result["cards"]
    assert result["trace"][-1]["status"] == "rejected"


def test_an_unavailable_item_must_not_become_an_outcome_even_over_native_http(tmp_path):
    class UnknownScale:
        def choose(self, view):
            return {
                "name": "compare_student_groups",
                "arguments": {
                    "conditions": [{"item_id": "Q01", "operator": "<=", "threshold": "20"}],
                    "outcomes": ["Q12"],
                },
            }

    result = run(
        "limited", budget_for(tmp_path, "limited"), transport=protocol_mock(UnknownScale())
    )
    assert not result["cards"]
    assert all(t["status"] == "rejected" for t in result["tool_ledger"])
    assert not result["verified_facts"]
