"""Issue #82: one server-owned live ledger, deduplicated by the human approval."""

from decimal import Decimal

import pytest
from prototypes.adaptive_assessment.live.budget import CampaignBudget, ledger_root
from prototypes.adaptive_assessment.live.demo import MOCK_DATE, mock_authorization
from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261112 as PRICE
from prototypes.qwen_execution.runtime import (
    ReviewedApproval,
    ReviewedBudget,
    code_fingerprint,
)

from app.ai.edu.discovery.models import AgentError

RECIPE = "profile-balanced-72"


def approval(campaign_id="live-ledger-campaign", **overrides):
    value = mock_authorization(RECIPE).model_dump()
    value.update(
        campaign_id=campaign_id,
        price_fingerprint=PRICE.fingerprint,
        valid_until=PRICE.valid_until,
        total_token_limit=5_955_648,
        cost_cap_cny=Decimal("4.78"),
        independent_review_reference="offline-test-not-real-review",
        reviewed_code_sha256=code_fingerprint(),
    )
    value.update(overrides)
    return ReviewedApproval.model_validate(value)


def test_live_journal_follows_the_server_root_whatever_the_working_directory(tmp_path, monkeypatch):
    first = ReviewedBudget(approval(), authorize=lambda: None)
    try:
        journal = ledger_root() / "live-ledger-campaign.jsonl"
        assert journal.is_file()
        elsewhere = tmp_path / "another-directory"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        with pytest.raises(AgentError, match="campaign_already_consumed"):
            ReviewedBudget(approval(), authorize=lambda: None)
    finally:
        first.close()


def test_live_budget_rejects_a_caller_supplied_directory(tmp_path):
    operator_picked = tmp_path / "operator-picked-ledger"
    with pytest.raises(AgentError, match="live_ledger_root_is_server_owned"):
        ReviewedBudget(approval(), operator_picked, authorize=lambda: None)
    assert not operator_picked.exists()


def test_one_human_approval_cannot_fund_a_second_campaign():
    first = ReviewedBudget(approval("approval-bound-campaign"), authorize=lambda: None)
    try:
        with pytest.raises(AgentError, match="approval_already_bound"):
            ReviewedBudget(approval("approval-bound-campaign-two"), authorize=lambda: None)
    finally:
        first.close()


def test_relative_ledger_root_fails_closed(monkeypatch):
    monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", "relative/ledger")
    with pytest.raises(AgentError, match="campaign_ledger_root_must_be_absolute"):
        ledger_root()


def test_offline_rehearsal_keeps_its_own_directory(tmp_path):
    budget = CampaignBudget(
        mock_authorization("five"), tmp_path, origin="http_mock", today=MOCK_DATE
    )
    try:
        assert (tmp_path / "mock-campaign-five.jsonl").is_file()
        assert not (ledger_root() / "mock-campaign-five.jsonl").exists()
    finally:
        budget.close()
