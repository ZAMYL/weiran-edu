"""Combined #82/#83/#95 public entry: no credentials, requests or real approvals."""

import json
import sys

import httpx
import pytest
from prototypes.adaptive_assessment.live.demo import MOCK_BASE
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.qwen_execution import ReviewedBudget, code_fingerprint, ledger_root
from prototypes.qwen_execution.cli import main
from pydantic import SecretStr

from app.ai.edu.discovery.models import AgentError
from app.ai.edu.qwen_config import QwenSettings

from .test_campaign_ledger_root import approval


def test_reserved_live_budget_cannot_restart_by_directory_or_campaign(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: pytest.fail("no network in ledger test"))
    source = admit("profile-balanced-72")
    settings = QwenSettings(SecretStr("offline-placeholder-only"), MOCK_BASE)
    original = approval("integrated-reserved-campaign")
    budget = ReviewedBudget(original, authorize=lambda: None)
    try:
        budget.claim(settings, source)
        assert budget.reserve() == 1
        before = budget.snapshot()
        assert before["origin"] == "qwen_live"
        assert before["entries"][0]["state"] == "reserved"
        assert before["charged_cny_upper_bound"] == "0.7956064"
    finally:
        budget.close()
    another_directory = tmp_path / "another-run"
    another_directory.mkdir()
    monkeypatch.chdir(another_directory)
    with pytest.raises(AgentError, match="campaign_already_consumed"):
        ReviewedBudget(original, authorize=lambda: None)
    rotated = type(original).model_validate(
        original.model_dump() | {"campaign_id": "integrated-rotated-campaign"}
    )
    with pytest.raises(AgentError, match="approval_already_bound"):
        ReviewedBudget(rotated, authorize=lambda: None)
    with pytest.raises(AgentError, match="live_ledger_root_is_server_owned"):
        ReviewedBudget(rotated, another_directory, authorize=lambda: None)
    assert not (ledger_root() / "integrated-rotated-campaign.jsonl").exists()
    last = json.loads(
        (ledger_root() / (original.campaign_id + ".jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    assert last == before


def test_unified_core_dry_run_never_reads_credentials_or_constructs_http(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run must not construct HTTP clients or read credentials")

    monkeypatch.setattr(QwenSettings, "from_env", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(sys, "argv", ["qwen-core-dry-run"])
    main()
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "dry_run_no_request"
    assert value["reviewed_code_sha256"] == code_fingerprint()
    assert value["real_qwen"] == "not_run" and not value["teacher_api_enabled"]
    assert (value["max_requests"], value["max_tool_attempts"], value["max_output_tokens"]) == (
        6,
        5,
        800,
    )
    assert value["conservative_six_request_cost_cny"] == "4.7736384"
    assert list(ledger_root().glob("*.jsonl")) == []
