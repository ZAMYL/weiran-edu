"""New readiness/security behavior, not additional statistical tuning cases."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from prototypes.adaptive_assessment.live.demo import MOCK_BASE, mock_authorization
from prototypes.adaptive_assessment.live.pricing import PRICE as OLD
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.research_agent_v2.runtime import ResearchExecutor, ResearchPlanner
from prototypes.research_agent_v22 import review
from prototypes.research_agent_v22.pilot import compare, mock_budget
from prototypes.research_agent_v22.preflight import (
    PRICE,
    RECIPE,
    proposal,
    validate_authorization,
)
from prototypes.research_agent_v22.profile import profile
from prototypes.research_agent_v22.provider import Lease, ProviderProbe, SyntheticProvider
from prototypes.research_agent_v22.scoring import grade

from app.ai.edu.discovery.models import AgentError, Limits

TODAY = date(2026, 11, 4)


def lease(provider):
    return provider.acquire(
        "synthetic-teacher",
        provider.source.envelope.data.batch.batch_id,
        provider.source.fingerprint,
        "synthetic-provider-campaign",
    )


def test_old_approval_not_renewed_by_new_price():
    assert OLD.valid_until == "2026-11-02" and OLD.fingerprint != PRICE.fingerprint
    a = mock_authorization(RECIPE)
    with pytest.raises(AgentError, match="stale_authorization_or_price"):
        a.check(TODAY)
    with pytest.raises(AgentError, match="stale_authorization_or_price"):
        a.model_copy(update={"price_fingerprint": PRICE.fingerprint}).check(TODAY)
    with pytest.raises(AgentError, match="stale_authorization_or_price"):
        a.model_copy(update={"price_fingerprint": "0" * 64}).check(TODAY)


def test_preflight_exact_worst_case_not_actual_bill():
    p = proposal(MOCK_BASE, today=TODAY)
    assert p["per_request_worst_tokens"] == 992608
    assert p["total_worst_tokens"] == 5955648 and p["output_token_cap"] == 4800
    assert Decimal(p["total_cost_upper_bound_cny"]) == Decimal("4.7736384")
    assert p["proposed_cost_cap_cny"] == "4.78"
    assert p["actual_cost_cny"] is None and p["real_qwen"] == "not_run"
    assert "price_reverification_required" in proposal(today=date(2026, 11, 12))["blockers"]
    with pytest.raises(AgentError):
        proposal("https://example.org/compatible-mode/v1", today=TODAY)


def test_current_authorization_binds_exact_source_and_capacity(tmp_path):
    budget = mock_budget(tmp_path, "capacity")
    try:
        validate_authorization(budget.authorization, MOCK_BASE, today=TODAY)
        assert budget.price == PRICE and budget.snapshot()["price_fingerprint"] == PRICE.fingerprint
        with pytest.raises(AgentError, match="pilot_authorization_mismatch"):
            validate_authorization(
                budget.authorization.model_copy(update={"max_requests": 5}), MOCK_BASE, today=TODAY
            )
        with pytest.raises(AgentError, match="stale_authorization_or_price"):
            budget.authorization.check(date(2026, 11, 12))
    finally:
        budget.close()


def test_unknown_usage_retains_new_price_reservation(tmp_path):
    from pydantic import SecretStr

    from app.ai.edu.qwen_config import QwenSettings

    budget = mock_budget(tmp_path, "unknown")
    try:
        budget.claim(QwenSettings(SecretStr("synthetic"), MOCK_BASE), admit(RECIPE))
        ticket = budget.reserve()
        with pytest.raises(AgentError, match="unverifiable_usage"):
            budget.settle(ticket, None, failed=True)
        snapshot = budget.snapshot()
        assert snapshot["charged_tokens"] == 992608
        assert snapshot["charged_cny_upper_bound"] == "0.7956064"
        assert snapshot["entries"][0]["state"] == "usage_unknown_reservation_held"
        with pytest.raises(AgentError, match="unverifiable_usage"):
            budget.reserve()
    finally:
        budget.close()


def test_reserved_request_profile_cannot_be_overridden(tmp_path):
    async def run():
        budget = mock_budget(tmp_path, "profile")
        executor = ResearchExecutor(admit(RECIPE), "probe-profile", Limits())
        from prototypes.research_agent_v2.demo import mock_transport
        from prototypes.research_agent_v2.goals import understand

        planner = ResearchPlanner(
            executor,
            budget,
            understand("优先核查评分异常", executor.kernel.catalog()),
            transport=mock_transport(),
        )
        try:
            with pytest.raises(AgentError, match="unsafe_request_profile"):
                await planner._post({"messages": [], "max_tokens": 999999})
            assert budget.snapshot()["request_count"] == 0
        finally:
            await planner.aclose()
            budget.close()

    asyncio.run(run())


def test_reviews_check_latest_state_and_exact_revisions(monkeypatch):
    head = "a" * 40
    state = {"approved": True, "commit": head}

    def fake(args):
        import json

        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return head
        if args[0] == "git":
            return ""
        number = 66 if "/66" in args[-1] else 67
        commit = review.DEPENDENCY_SHA if number == 66 else head
        if args[-1].endswith("reviews"):
            return json.dumps(
                [
                    [
                        {
                            "id": 1,
                            "user": {"login": review.REVIEWER},
                            "state": "APPROVED" if state["approved"] else "CHANGES_REQUESTED",
                            "commit_id": commit if number == 66 else state["commit"],
                        }
                    ]
                ]
            )
        return json.dumps({"head": {"sha": commit}})

    monkeypatch.setattr(review, "command", fake)
    assert review.verify_reviews(67)["status"] == "approved"
    state["approved"] = False
    with pytest.raises(AgentError, match="independent_review_required"):
        review.verify_reviews(67)
    state.update(approved=True, commit="b" * 40)
    with pytest.raises(AgentError, match="independent_review_required"):
        review.verify_reviews(67)


def test_provider_rejects_lease_source_substitution_and_revocation():
    provider = SyntheticProvider()
    current = lease(provider)
    from types import SimpleNamespace

    fake = SimpleNamespace(
        fingerprint=current.source.fingerprint,
        envelope=current.source.envelope,
        validate=lambda: None,
    )
    with pytest.raises(AgentError, match="non_synthetic_provider_not_enabled"):
        ProviderProbe(provider, Lease(current.binding, fake))
    with pytest.raises(AgentError, match="provider_lease_source_mismatch"):
        ProviderProbe(provider, Lease(current.binding, admit("research-v2-capability")))
    probe = ProviderProbe(provider, current)
    assert len(probe.metadata()["capabilities"]["items"]) == 4
    provider.enabled = False
    with pytest.raises(AgentError, match="provider_authorization_revoked"):
        probe.execute("get_dimension_statistics", {})
    assert not probe.executor.facts


def test_provider_actual_statistics_witness_and_conditions():
    provider = SyntheticProvider()
    probe = ProviderProbe(provider, lease(provider))
    summary = probe.execute("get_dimension_statistics", {})
    assert summary[0]["items"][2]["mean"]["numerator"] == 25
    group = probe.execute(
        "compare_student_groups",
        {
            "conditions": [
                {"item_id": "Q01", "operator": "<=", "threshold": "0"},
                {"item_id": "Q02", "operator": "==", "threshold": "0"},
            ],
            "outcomes": ["Q03"],
        },
    )
    assert group[0]["metrics"]["target_count"] == 10
    fact = list(probe.executor.facts.values())[-1]
    source = fact.item_sources[0]
    w = provider.replay_witness(probe.binding, source.references[0])
    assert w.source_sha256 == probe.binding.snapshot_id
    assert probe.goal("比较Q01、Q02的组合与Q03的群体差异")["type"] == "group"
    with pytest.raises(AgentError, match="provider_unknown_witness"):
        provider.replay_witness(probe.binding, "forged")


def test_provider_failure_rolls_back_and_shared_attempts_do_not_reset():
    provider = SyntheticProvider()
    current = lease(provider)
    for _ in range(5):
        probe = ProviderProbe(provider, current)
        with pytest.raises(AgentError):
            probe.execute("unknown", {})
        assert not probe.executor.facts and probe.executor.output_bytes == 0
    with pytest.raises(AgentError, match="tool_budget_exhausted"):
        ProviderProbe(provider, current).execute("get_dimension_statistics", {})


def test_provider_postcheck_rejection_retains_no_new_fact(monkeypatch):
    provider = SyntheticProvider()
    probe = ProviderProbe(provider, lease(provider))

    def revoke(binding, released):
        provider.enabled = False
        raise AgentError("provider_authorization_revoked")

    monkeypatch.setattr(provider, "commit_disclosure", revoke)
    with pytest.raises(AgentError, match="provider_authorization_revoked"):
        probe.execute("get_dimension_statistics", {})
    assert not probe.executor.facts and probe.executor.output_bytes == 0


def test_profile_explains_bounded_ledger_reuse_without_removing_checks():
    result = asyncio.run(profile())
    assert 0 < result["observed_replays"] == result["reported_replays"]
    assert result["observed_replays"] < result["naive_replays_without_reuse"]
    assert result["full_ledger_replays_avoided"] == (
        result["naive_replays_without_reuse"] - result["observed_replays"]
    )
    assert result["checks_removed"] == 0 and result["tool_attempts"] == 3
    assert result["reuse_scope"] == "single_executor_unchanged_ledger"


def test_three_arm_offline_is_not_real_qwen_and_no_key_read(tmp_path, monkeypatch):
    from app.ai.edu.qwen_config import QwenSettings

    def denied():
        raise AssertionError("offline attempted credential access")

    monkeypatch.setattr(QwenSettings, "from_env", denied)
    result = asyncio.run(compare(tmp_path))
    assert result["actual_qwen"] == "not_run" and result["arms"]["C_qwen"]["status"] == "not_run"
    for key in ("A_fixed", "B_http_mock"):
        arm = result["arms"][key]
        assert arm["score"]["target_denominator"] == 9
        assert not arm["score"]["wrong_facts"] and not arm["score"]["source_errors"]
        assert arm["actual_tokens"] is arm["invoice_cost_cny"] is None
        assert arm["run"]["tool_attempts"] <= 5
        assert arm["run"]["budget"]["price_fingerprint"] == PRICE.fingerprint


def test_scoring_accepts_other_legal_conditions_without_changing_oracle():
    executor = ResearchExecutor(admit(RECIPE), "legal-condition", Limits())
    executor.execute("get_dimension_statistics", executor.parse("get_dimension_statistics", {}))
    seeds = list(executor.facts)
    args = {
        "conditions": [{"item_id": "Q01", "operator": ">=", "threshold": "10"}],
        "outcomes": ["Q03"],
    }
    executor.execute("compare_student_groups", executor.parse("compare_student_groups", args))
    result = {
        "retained_fact_ids": seeds,
        "tool_ledger": [],
        "model_requests": 0,
        "tool_attempts": 2,
        "stop_reason": "stopped_with_uncovered_directions",
        "investigation_coverage": {"uncovered_direction_ids": ["remaining"]},
        "elapsed_seconds": 0,
    }
    actual = grade(result, executor)
    assert actual["wrong_facts"] == actual["source_errors"] == 0
    assert len(actual["checked_semantics"]) == 5


def test_live_missing_independent_review_stops_before_credentials(tmp_path, monkeypatch):
    from prototypes.research_agent_v22 import pilot

    from app.ai.edu.qwen_config import QwenSettings

    a = mock_budget(tmp_path / "draft", "blocked-review")
    try:
        approval = pilot.PilotApproval(
            plan_sha256=pilot.PLAN_SHA256, code_revision="a" * 40, authorization=a.authorization
        )
    finally:
        a.close()
    monkeypatch.setattr(pilot, "command", lambda args: "a" * 40)
    original_validate = pilot.validate_authorization
    monkeypatch.setattr(
        pilot,
        "validate_authorization",
        lambda auth, endpoint: original_validate(auth, endpoint, today=TODAY),
    )

    def reject_review(number):
        raise AgentError("independent_review_required")

    def reject_key():
        raise AssertionError("key read before independent review")

    monkeypatch.setattr(pilot, "verify_reviews", reject_review)
    monkeypatch.setattr(QwenSettings, "from_env", reject_key)
    with pytest.raises(AgentError, match="independent_review_required"):
        asyncio.run(
            pilot.compare(
                tmp_path / "never-live", approval=approval, review_pr=67, endpoint=MOCK_BASE
            )
        )
    assert not (tmp_path / "never-live").exists()


def test_actual_http_body_respects_non_thinking_profile(tmp_path):
    import json

    import httpx
    from prototypes.adaptive_assessment.live.demo import native_response
    from prototypes.research_agent_v2.runtime import investigate

    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert body["model"] == "qwen3.8-flash" and body["max_tokens"] == 800
        assert body["enable_thinking"] is False and body["stream"] is False
        return httpx.Response(
            200,
            json=native_response(
                {
                    "name": "finish",
                    "arguments": {"selected_fact_ids": [], "reason": "no_valid_findings"},
                }
            ),
        )

    async def run():
        budget = mock_budget(tmp_path, "http-profile")
        try:
            result, _ = await investigate(
                RECIPE, "优先核查评分异常", budget, transport=httpx.MockTransport(handler)
            )
            assert result["budget"]["request_count"] == len(bodies) == 1
            assert result["actual_tokens"] is None and result["real_qwen"] == "not_run"
        finally:
            budget.close()

    asyncio.run(run())


def test_provider_rejects_wrong_batch_snapshot_scope_and_history():
    from dataclasses import replace

    provider = SyntheticProvider()
    with pytest.raises(AgentError, match="provider_access_denied"):
        provider.acquire("other-teacher", "wrong", "0" * 64, "campaign")
    with pytest.raises(AgentError, match="provider_snapshot_mismatch"):
        provider.acquire("synthetic-teacher", "wrong", "0" * 64, "campaign")
    current = lease(provider)
    with pytest.raises(AgentError, match="provider_context_mismatch"):
        ProviderProbe(
            provider, replace(current, binding=replace(current.binding, scope_sha256="0" * 64))
        )
    provider._history.append(frozenset(["forged-member"]))
    with pytest.raises(AgentError, match="provider_context_mismatch"):
        ProviderProbe(provider, current)


def test_new_prototype_style_is_included_in_ci():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    paths = ["prototypes/research_agent_v22"]
    for arguments in (("check",), ("format", "--check")):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                *arguments,
                "--no-cache",
                "--config",
                "apps/api/pyproject.toml",
                *paths,
            ],
            cwd=root,
            check=True,
        )


def test_live_budget_is_refused_and_report_path_cannot_change_fixed_root(tmp_path, monkeypatch):
    from prototypes.adaptive_assessment.live.safety import LIVE_DISABLED
    from prototypes.research_agent_v22 import pilot

    from app.ai.edu.qwen_config import QwenSettings

    def prohibited():
        raise AssertionError("must_not_read_credentials")

    monkeypatch.setattr(QwenSettings, "from_env", prohibited)
    root = tmp_path / "fixed-live-journals"
    monkeypatch.setattr(pilot, "LIVE_LEDGER_ROOT", root)
    mock = mock_budget(tmp_path / "mock", "shared-live-campaign")
    try:
        approval = pilot.PilotApproval(
            plan_sha256=pilot.PLAN_SHA256, code_revision="a" * 40, authorization=mock.authorization
        )
    finally:
        mock.close()
    calls = []
    original = pilot.CampaignBudget

    def checked_budget(auth, state, *, origin):
        calls.append((auth.campaign_id, state, origin))
        return original(auth, state, origin=origin)

    monkeypatch.setattr(pilot, "CampaignBudget", checked_budget)
    for report in (tmp_path / "report-A", tmp_path / "report-B"):
        report.mkdir()
        with pytest.raises(AgentError, match=LIVE_DISABLED):
            pilot.open_live_budget(approval)
    assert calls == [(approval.authorization.campaign_id, root, "synthetic_live")] * 2
    assert not root.exists()
    assert not list(tmp_path.rglob("fixed-live-journals/*.jsonl"))


def test_cli_does_not_attribute_stale_failure_to_new_rejected_run(tmp_path, monkeypatch):
    import json
    import sys

    from prototypes.research_agent_v22 import evaluate

    output = tmp_path / "new-report.json"
    old = tmp_path / "new-report-campaigns" / "C_qwen"
    old.mkdir(parents=True)
    (old / "failure.json").write_text('{"actual_qwen":"attempted"}', encoding="utf-8")
    invalid = tmp_path / "invalid-approval.json"
    invalid.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate",
            "--execute",
            "--authorization",
            str(invalid),
            "--review-pr",
            "67",
            "--output",
            str(output),
        ],
    )
    assert evaluate.main() == 2
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["real_qwen"] == "not_run" and "partial_failure" not in result


def test_auxiliary_failure_write_error_preserves_this_attempt_status(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from prototypes.research_agent_v22 import pilot

    def fail_write(*args, **kwargs):
        raise OSError("simulated local private path must not be reported")

    monkeypatch.setattr(Path, "open", fail_write)
    snapshot = {"request_count": 1, "entries": [{"transport_status": "model_timeout"}]}
    budget = SimpleNamespace(snapshot=lambda: snapshot)
    current = {"execution_id": "this-run", "arms": {}}
    failure = pilot.failed_run(current, budget, "test-campaign", RuntimeError())
    assert failure.partial["actual_qwen"] == "attempted"
    assert failure.partial["failure_journal_error"] == "auxiliary_failure_record_unavailable"
    assert "private path" not in str(failure.partial)
    assert (
        pilot.transport_state({"request_count": 1, "entries": [{}]})
        == "request_reserved_transport_unconfirmed"
    )
