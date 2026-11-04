"""Reviewer P1 counterexamples. Synthetic 2026-10-26 clock, NO keys or network."""

import asyncio
import json
import sys
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.cli import execute as legacy_execute
from prototypes.adaptive_assessment.live.demo import MOCK_BASE, MOCK_DATE, mock_authorization
from prototypes.adaptive_assessment.live.planner import QwenAdaptivePlanner, run_synthetic_live
from prototypes.adaptive_assessment.live.safety import LIVE_DISABLED
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.research_agent_v2 import demo
from prototypes.research_agent_v2.goals import understand
from prototypes.research_agent_v2.runtime import ResearchExecutor, ResearchPlanner, investigate
from pydantic import SecretStr

from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.qwen_config import QwenSettings


def test_copied_authorization_cannot_prepare_live_or_reserve_again(tmp_path, monkeypatch):
    # Use the review's original effective window. Expiry today must not conceal the
    # vulnerable code path; never update production price/authorization dates.
    monkeypatch.setattr(CampaignBudget, "today", property(lambda _: MOCK_DATE))
    events = []
    key_loads = []

    def substituted_settings():
        key_loads.append("synthetic_substitute_only")
        return QwenSettings(SecretStr("offline-review-placeholder"), MOCK_BASE)

    monkeypatch.setattr(QwenSettings, "from_env", substituted_settings)

    def no_network(*args, **kwargs):
        raise AssertionError("network_client_must_not_be_created")

    monkeypatch.setattr(httpx, "AsyncClient", no_network)
    monkeypatch.setattr(httpx, "Client", no_network)

    async def offline_probe(recipe, question, budget, *, allow_network=False):
        executor = ResearchExecutor(admit(recipe), "review-probe", Limits())
        goal = understand(question, executor.kernel.catalog())
        planner = ResearchPlanner(executor, budget, goal, allow_network=allow_network)
        try:
            budget.reserve()
            events.append(budget.snapshot())
        finally:
            await planner.aclose()
        return {"status": "blocked", "real_qwen": "not_run"}, executor

    monkeypatch.setattr(demo, "investigate", offline_probe)
    authorization = mock_authorization(
        "research-v2-capability",
        campaign_id="review66-same-campaign",
        max_requests=1,
        total_token_limit=992608,
        cost_cap_cny="0.8",
    ).model_dump_json()
    for folder in ("original", "copied"):
        directory = tmp_path / folder
        directory.mkdir()
        path = directory / "authorization.json"
        path.write_text(authorization, encoding="utf-8")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "research-demo",
                "--execute",
                "--authorization",
                str(path),
                "--output",
                str(directory / "report.json"),
            ],
        )
        try:
            demo.main()
        except SystemExit as exit:
            assert exit.code == 2
    proof = {
        "fixture_date": MOCK_DATE.isoformat(),
        "price_or_authorization_dates_changed": False,
        "live_preparations": len(events),
        "credential_loader_substitute_calls": len(key_loads),
        "reserved_requests": sum(e["request_count"] for e in events),
        "reserved_tokens": sum(e["charged_tokens"] for e in events),
        "reserved_cny": str(
            sum((Decimal(e["charged_cny_upper_bound"]) for e in events), Decimal(0))
        ),
        "real_model_requests": 0,
        "real_key_reads": 0,
    }
    (tmp_path / "copied-authorization-proof.json").write_text(json.dumps(proof), encoding="utf-8")
    print(json.dumps(proof))
    assert events == [] and key_loads == []
    assert not list(tmp_path.rglob("*.jsonl"))


@pytest.mark.parametrize(
    "layer", ["budget", "planner", "adaptive_runtime", "research_runtime", "legacy_cli"]
)
def test_all_live_layers_fail_before_credentials_network_or_ledger(layer, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must_not_read_key_or_create_network_client")

    monkeypatch.setattr(QwenSettings, "from_env", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    budget = SimpleNamespace(origin="synthetic_live")
    root = tmp_path / "must-not-create"
    with pytest.raises(AgentError, match=LIVE_DISABLED):
        if layer == "budget":
            CampaignBudget(object(), root, origin="synthetic_live")
        elif layer == "planner":
            QwenAdaptivePlanner(None, budget, allow_network=True)
        elif layer == "adaptive_runtime":
            asyncio.run(run_synthetic_live("not-a-source", budget, allow_network=True))
        elif layer == "research_runtime":
            asyncio.run(investigate("not-a-source", "not-a-goal", budget, allow_network=True))
        else:
            asyncio.run(legacy_execute("five", root / "authorization.json"))
    assert not root.exists()


@pytest.mark.parametrize("previous", ["failed", "cancelled"])
def test_failure_or_cancellation_never_restores_campaign_and_live_stays_closed(
    previous, tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("must_not_load_key")

    monkeypatch.setattr(QwenSettings, "from_env", forbidden)
    root = tmp_path / "operator-offline-ledger"
    auth = mock_authorization(
        "research-v2-capability",
        campaign_id="review66-failed-campaign",
        max_requests=1,
        total_token_limit=992608,
        cost_cap_cny="0.8",
    )
    budget = CampaignBudget(auth, root, origin="http_mock", today=MOCK_DATE)
    try:
        budget.claim(
            QwenSettings(SecretStr("offline-placeholder"), MOCK_BASE),
            admit("research-v2-capability"),
        )
        ticket = budget.reserve()
        if previous == "failed":
            with pytest.raises(AgentError, match="unverifiable_usage"):
                budget.settle(ticket, None, failed=True)
    finally:
        budget.close()
    journal = json.loads((root / (auth.campaign_id + ".jsonl")).read_text().splitlines()[-1])
    assert journal["request_count"] == 1 and journal["charged_tokens"] == 992608
    assert Decimal(journal["charged_cny_upper_bound"]) == Decimal("0.7956064")
    with pytest.raises(AgentError, match="campaign_already_consumed"):
        CampaignBudget(auth, root, origin="http_mock", today=MOCK_DATE)
    copied = tmp_path / "different-authorization-parent"
    copied.mkdir()
    authorization = copied / "authorization.json"
    authorization.write_text(auth.model_dump_json())
    for _ in range(2):
        with pytest.raises(AgentError, match=LIVE_DISABLED):
            asyncio.run(legacy_execute("research-v2-capability", authorization))
        with pytest.raises(AgentError, match=LIVE_DISABLED):
            CampaignBudget(auth, copied / "new-ledger", origin="synthetic_live")
    assert not (copied / "new-ledger").exists()


def test_execute_rejected_before_inspecting_authorization_or_output(tmp_path, monkeypatch):
    class NoFileAccess:
        def __init__(self, value):
            pass

        def exists(self):
            raise AssertionError("must_not_inspect_output_before_live_denial")

        def stat(self):
            raise AssertionError("must_not_read_authorization")

        def read_bytes(self):
            raise AssertionError("must_not_read_authorization")

    monkeypatch.setattr(demo, "Path", NoFileAccess)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "research-demo",
            "--execute",
            "--authorization",
            "unapproved.json",
            "--output",
            "unused.json",
        ],
    )
    with pytest.raises(SystemExit) as exit:
        demo.main()
    assert exit.value.code == 2
