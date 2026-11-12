"""Real adapter path with intercepted HTTP; never credentials or paid requests."""

import asyncio
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import httpx
import pytest
from app.ai.edu.discovery.models import AgentError
from app.ai.edu.qwen_config import QwenSettings
from pydantic import SecretStr

from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.demo import MOCK_BASE, mock_authorization, native_response
from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261112 as PRICE
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.capability_profiles.agent import ProfileMockPolicy
from prototypes.qwen_execution.runtime import (
    ReviewedApproval,
    ReviewedBudget,
    code_fingerprint,
    run_reviewed_profiles,
)

RECIPE = "profile-balanced-72"
SETTINGS = QwenSettings(SecretStr("synthetic-placeholder-only"), MOCK_BASE)


def approval(**overrides):
    value = mock_authorization(RECIPE).model_dump()
    value.update(
        campaign_id="reviewed-test-campaign",
        price_fingerprint=PRICE.fingerprint,
        valid_until=PRICE.valid_until,
        total_token_limit=5_955_648,
        cost_cap_cny=Decimal("4.78"),
        independent_review_reference="offline-test-not-real-review",
        reviewed_code_sha256=code_fingerprint(),
    )
    value.update(overrides)
    return ReviewedApproval.model_validate(value)


def test_reviewed_multiround_preserves_actual_facts_witnesses_and_privacy(tmp_path):
    captured = []

    def respond(request):
        body = json.loads(request.content)
        captured.append(body)
        view = json.loads(body["messages"][-1]["content"])
        return httpx.Response(
            200, json=native_response(ProfileMockPolicy().choose(view), len(captured))
        )

    budget = ReviewedBudget(
        approval(), tmp_path, authorize=lambda: None, transport=httpx.MockTransport(respond)
    )
    try:
        result, _, executor = asyncio.run(run_reviewed_profiles(RECIPE, budget, SETTINGS))
        assert result["status"] == "completed", result["stop_reason"]
        assert result["origin"] == "http_mock" and result["real_qwen"] == "not_run"
        assert 2 <= result["model_requests"] == len(captured) <= 6
        assert result["tool_attempts"] <= 5 and result["profiles"]
        assert result["source_replay_verified"]
        assert budget.snapshot()["charged_tokens"] == sum(
            1100 + 100 * i for i in range(1, len(captured) + 1)
        )
        for profile in result["profiles"]:
            assert profile["selection_origin"] == "http_mock"
            for fid in profile["verified_fact_ids"]:
                fact = executor.facts[fid]
                assert fact.view["qualified"]
                assert executor.resolve(fid, "target.valid")
        raw = json.dumps(captured)
        for secret in (
            "participants",
            "reference_id",
            "evidence_id",
            "source_sha256",
            "answers/",
            "S0001",
        ):
            assert secret not in raw
        assert all(p["parallel_tool_calls"] is False for p in captured)
    finally:
        budget.close()


def test_legacy_live_stays_disabled_and_review_code_is_bound(tmp_path):
    with pytest.raises(AgentError, match="disabled_pending_independent_review"):
        CampaignBudget(mock_authorization("five"), tmp_path, origin="synthetic_live")
    with pytest.raises(AgentError, match="reviewed_code_changed"):
        approval(reviewed_code_sha256="0" * 64).check(date(2026, 11, 12))


def test_revocation_after_response_rejects_publication_and_holds_reservation(tmp_path):
    revoked = False

    def authorize():
        if revoked:
            raise AgentError("purpose_revoked")

    def respond(request):
        nonlocal revoked
        revoked = True
        return httpx.Response(
            200,
            json=native_response(
                {
                    "name": "finish",
                    "arguments": {"selected_fact_ids": [], "reason": "no_valid_findings"},
                }
            ),
        )

    budget = ReviewedBudget(
        approval(), tmp_path, authorize=authorize, transport=httpx.MockTransport(respond)
    )
    try:
        with pytest.raises(AgentError, match="purpose_revoked"):
            asyncio.run(run_reviewed_profiles(RECIPE, budget, SETTINGS))
        assert budget.snapshot()["request_count"] == 1
        assert budget.snapshot()["entries"][0]["state"] == "reserved"
    finally:
        budget.close()


def test_shared_budget_does_not_reset_and_source_cannot_change(tmp_path):
    budget = ReviewedBudget(
        approval(max_requests=1),
        tmp_path,
        authorize=lambda: None,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    source = admit(RECIPE)
    try:
        budget.claim(SETTINGS, source)
        with pytest.raises(AgentError, match="campaign_run_already_claimed"):
            budget.claim(SETTINGS, source)
        with pytest.raises(AgentError, match="authorization_scope_mismatch"):
            budget.check(SETTINGS, replace(source, fingerprint="0" * 64))
        budget.reserve()
        with pytest.raises(AgentError, match="request_already_in_flight"):
            budget.reserve()
    finally:
        budget.close()
    with pytest.raises(AgentError, match="campaign_already_consumed"):
        ReviewedBudget(approval(max_requests=1), tmp_path, authorize=lambda: None)


def test_teacher_objects_and_insufficient_cost_are_rejected_before_request(tmp_path):
    budget = ReviewedBudget(
        approval(cost_cap_cny=Decimal("0.01")),
        tmp_path,
        authorize=lambda: None,
        transport=httpx.MockTransport(lambda _: pytest.fail("network must not be reached")),
    )
    try:
        with pytest.raises(AgentError, match="remote_teacher_source_not_enabled"):
            budget.check(SETTINGS, object())
        budget.claim(SETTINGS, admit(RECIPE))
        with pytest.raises(AgentError, match="authorized_cost_limit"):
            budget.reserve()
        assert budget.snapshot()["request_count"] == 0
    finally:
        budget.close()
