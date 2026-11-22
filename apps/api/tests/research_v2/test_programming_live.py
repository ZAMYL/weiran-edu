"""LIVE-01 boundary tests. No test can create an actual HTTP client with network access."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import httpx
import pytest
from prototypes.adaptive_assessment.live import budget, pricing
from prototypes.adaptive_assessment.live.demo import native_response
from prototypes.durable_teacher_agent import live_admission
from prototypes.qwen_execution.runtime import code_fingerprint
from prototypes.research_agent_v2 import programming_live as live
from prototypes.research_agent_v2.programming import INSPECT, OVERVIEW
from prototypes.research_agent_v2.programming_live_cli import BASE, _binding, _prepare

from app.ai.edu.discovery.models import AgentError
from app.edu.auth import TeacherAdmin
from evaluation.ds06a.agent_trace import AgentStep, AgentTrace, _validate_trace


@pytest.fixture
def approved_setup(tmp_path, monkeypatch):
    async def no_network(*_args, **_kwargs):
        pytest.fail("a test attempted to open the network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    root = tmp_path / "state"
    ledger = tmp_path / "campaigns"
    approval_path = tmp_path / "approval.json"
    monkeypatch.setenv(live.APPROVAL_ENV, str(approval_path))
    monkeypatch.setenv(live_admission.LEDGER_ROOT_ENV, str(ledger))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-placeholder-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", BASE)
    monkeypatch.setenv("WEIRAN_QWEN_MODEL", "qwen3.8-flash")
    monkeypatch.delenv(live.APPROVAL_ENV)
    registration = _prepare(root)
    assert registration["ready"] is False
    assert registration["network_attempted"] is False
    monkeypatch.setenv(live.APPROVAL_ENV, str(approval_path))

    today = datetime.now(UTC).date()
    price = replace(
        pricing.REVIEWED_PRICE_20261119,
        checked_on=(today - timedelta(days=1)).isoformat(),
        valid_until=(today + timedelta(days=1)).isoformat(),
    )
    original = pricing.reviewed_price

    def resolve(fingerprint):
        return price if fingerprint == price.fingerprint else original(fingerprint)

    for module in (budget, live, live_admission):
        monkeypatch.setattr(module, "reviewed_price", resolve)
    scope = registration["scope"]
    fields = {
        "campaign_id": "live-01-test",
        "human_approval_reference": "test-fixture-only",
        "independent_review_reference": "https://github.com/ZAMYL/weiran-edu/pull/129#issuecomment-1",
        "purpose": live.PURPOSE,
        "model": "qwen3.8-flash",
        "endpoint_sha256": sha256(BASE.encode()).hexdigest(),
        "source_fingerprints": {scope["assessment_id"]: scope["snapshot_id"]},
        "price_fingerprint": price.fingerprint,
        "valid_until": price.valid_until,
        "max_requests": 6,
        "max_output_tokens": 800,
        "total_token_limit": 5_955_648,
        "cost_cap_cny": "4.78",
        "reviewed_code_sha256": code_fingerprint(),
        "teacher_id": scope["teacher_id"],
        "task_id": scope["task_id"],
        "zip_sha256": live.ZIP_SHA256,
        "ledger_root_sha256": live.ledger_fingerprint(ledger),
    }

    def approve(**overrides):
        approval_path.write_text(json.dumps({**fields, **overrides}), encoding="utf-8")
        return _binding(root)

    return root, ledger, approve


def dialog(binding, *, after=None, usage=True, exhaust=False):
    seen = []

    def respond(request):
        payload = json.loads(request.content)
        assert payload["enable_thinking"] is False
        assert payload["max_tokens"] == 800
        decoded = request.content.decode()
        for private in (
            "SYN000",
            "虚构参评者",
            "values = [61302]",
            "synthetic-private",
            "synthetic-live-problem",
            "test-placeholder-key",
            binding.provider.scope.actor,
            binding.provider.scope.batch,
            binding.provider.scope.snapshot,
            binding.provider.scope.task,
            live.ZIP_SHA256,
        ):
            assert private not in decoded
        view = json.loads(payload["messages"][-1]["content"])
        assert set(view) == {
            "analysis_context",
            "facts",
            "last_result",
            "remaining_model_requests",
            "remaining_tool_attempts",
        }
        facts = view["facts"]
        if not facts:
            decision = {"name": OVERVIEW, "arguments": {}}
        elif len(facts) == 1:
            overview = facts[0]
            direction = next(
                item for item in overview["directions"] if item["phenomenon"] == "timeout"
            )
            decision = {
                "name": INSPECT,
                "arguments": {
                    "overview_id": overview["fact_id"],
                    "direction": f"D9{len(seen)}" if exhaust else direction["direction"],
                },
                "basis_fact_ids": [overview["fact_id"]],
            }
        else:
            decision = {
                "name": "finish",
                "arguments": {"reason": "completed", "selected_fact_ids": [facts[1]["fact_id"]]},
                "basis_fact_ids": [facts[1]["fact_id"]],
            }
        seen.append(decision["name"])
        if after is not None:
            after(len(seen))
        return httpx.Response(200, json=native_response(decision, len(seen), usage=usage))

    return httpx.MockTransport(respond), seen


def test_multiround_replay_and_mock_provenance(approved_setup):
    root, ledger, approve = approved_setup
    binding = approve()
    transport, seen = dialog(binding)
    result = asyncio.run(live._run(binding, transport=transport))
    assert result["state"] == "completed", result
    assert seen == [OVERVIEW, INSPECT, "finish"]
    assert result["execution_origin"] == "http_mock"
    assert result["ds06b"]["origin"] == "http_mock"
    assert result["ds06b"]["model_origin_verified"] is False
    assert (result["model_requests"], result["tool_attempts"], result["charged_tokens"]) == (
        3,
        2,
        3900,
    )
    assert result["charged_cny_upper_bound"] == "0.00348"
    finding = result["result"]["findings"][0]
    direction = finding["direction"]
    assert (direction["phenomenon"], direction["affected"], direction["covered"]) == (
        "timeout",
        5,
        15,
    )
    assert "cause_unconfirmed" in finding["limitations"]
    assert any(
        source["witness_count"] == 5 and source["metric_key"].endswith(".affected")
        for fact in result["result"]["facts"]
        for source in fact["witnesses"]
    )
    steps = tuple(AgentStep(**step) for step in result["ds06b"]["steps"])
    with pytest.raises(AgentError, match="model_origin_required"):
        _validate_trace(AgentTrace("http_mock", "P1", steps))
    # Check the role and usage conversion against DS-06B's existing structural contract only.
    _validate_trace(AgentTrace("approved_qwen", "P1", steps))
    assert not list(ledger.glob("*.jsonl"))
    with pytest.raises(AgentError, match="programming_live_readback_unavailable"):
        live.replay_programming_live(_binding(root))
    reread = live._replay(_binding(root), origin="http_mock")
    assert reread == json.loads(json.dumps(result))
    with pytest.raises(AgentError, match="campaign_already_consumed"):
        asyncio.run(live._run(_binding(root), transport=transport))
    path = binding.provider.reader.originals.path_for(live.ZIP_SHA256)
    path.write_bytes(b"corrupt synthetic fixture")
    with pytest.raises(AgentError, match="source"):
        live._replay(_binding(root), origin="http_mock")


def test_live_ledger_single_approval_and_fixed_root(approved_setup, monkeypatch):
    _, ledger, approve = approved_setup
    binding = approve()
    campaign = live._ProgrammingCampaign(binding, transport=None)
    campaign.close()
    assert (ledger / "live-01-test.jsonl").is_file()
    with pytest.raises(AgentError, match="campaign_already_consumed"):
        live._ProgrammingCampaign(binding, transport=None)
    replacement = approve(campaign_id="live-01-changed")
    with pytest.raises(AgentError, match="approval_already_bound"):
        live._ProgrammingCampaign(replacement, transport=None)
    monkeypatch.setenv(live_admission.LEDGER_ROOT_ENV, str(ledger.with_name("rotated")))
    with pytest.raises(AgentError, match="programming_live_approval_changed"):
        asyncio.run(live.run_programming_live(replacement))
    with pytest.raises(AgentError, match="programming_live_scope_mismatch"):
        asyncio.run(live.run_programming_live(approve(campaign_id="another-live-id")))


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("reviewed_code_sha256", "0" * 64, "reviewed_code_changed"),
        ("valid_until", "1999-01-01", "stale_authorization_or_price"),
        ("purpose", "database_live", "programming_live_approval_invalid"),
        ("zip_sha256", "0" * 64, "programming_live_approval_invalid"),
        ("independent_review_reference", "placeholder", "programming_live_approval_invalid"),
        ("max_output_tokens", 801, "programming_live_approval_invalid"),
        ("cost_cap_cny", "5.01", "programming_live_approval_invalid"),
    ],
)
def test_invalid_approval_has_no_campaign(approved_setup, field, value, error):
    _, ledger, approve = approved_setup
    with pytest.raises(AgentError, match=error):
        approve(**{field: value})
    assert not ledger.exists()


@pytest.mark.parametrize("requests", [1, 3])
def test_revoke_during_response_fails_closed(approved_setup, requests):
    _, _, approve = approved_setup
    binding = approve()

    def revoke(count):
        if count == requests:
            TeacherAdmin(binding.provider.reader.database).revoke_tokens(
                binding.provider.scope.actor
            )

    transport, seen = dialog(binding, after=revoke)
    receipt = asyncio.run(live._run(binding, transport=transport))
    assert len(seen) == requests
    assert receipt["state"] == "failed"
    assert receipt["reason"] == "programming_live_authorization_revoked"
    assert "result" not in receipt
    assert receipt["model_requests"] == requests
    assert receipt["http_observations"][-1]["http_status"] == 200


@pytest.mark.parametrize(
    ("fields", "usage", "reason", "http_calls"),
    [
        ({"cost_cap_cny": "0.01"}, True, "authorized_cost_limit", 0),
        ({"total_token_limit": 1000}, True, "authorized_token_limit", 0),
        ({}, False, "unverifiable_usage", 1),
    ],
)
def test_budget_and_unknown_usage(approved_setup, fields, usage, reason, http_calls):
    _, _, approve = approved_setup
    binding = approve(**fields)
    transport, seen = dialog(binding, usage=usage)
    receipt = asyncio.run(live._run(binding, transport=transport))
    assert (receipt["state"], receipt["reason"], len(seen)) == ("failed", reason, http_calls)
    assert "result" not in receipt
    if http_calls:
        assert receipt["entries"][-1]["state"] == "usage_unknown_reservation_held"
        assert receipt["charged_cny_upper_bound"] == receipt["entries"][-1]["reserved_cny"]


def test_six_model_five_tool_limits(approved_setup):
    _, _, approve = approved_setup
    binding = approve()
    transport, seen = dialog(binding, exhaust=True)
    receipt = asyncio.run(live._run(binding, transport=transport))
    assert len(seen) == receipt["model_requests"] == 6
    assert receipt["tool_attempts"] == 5
    assert receipt["reason"] == "tool_budget_exhausted"
    assert "result" not in receipt


def test_removing_independent_approval_blocks_network_and_ledger(approved_setup, monkeypatch):
    _, ledger, approve = approved_setup
    binding = approve()
    monkeypatch.delenv(live.APPROVAL_ENV)
    with pytest.raises(AgentError, match="live_admission_not_configured"):
        asyncio.run(live.run_programming_live(binding))
    assert not ledger.exists()
