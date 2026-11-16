"""Run explicitly in a disposable tree combining this patch with PR #98's fixed SHA.

No mocked admission or paid requests: use #98's actual gate with test-only permits.
"""

import asyncio
import json
from dataclasses import replace
from hashlib import sha256

import pytest
from app.ai.edu.discovery.models import AgentError
from tests.durable_teacher_agent.test_qwen_database_offline import (
    assert_database_profiles,
    transport_for,
)
from tests.durable_teacher_agent.test_qwen_database_offline import (
    profile_binding as profile_binding,
)

from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261112 as PRICE
from prototypes.durable_teacher_agent import live_admission
from prototypes.qwen_execution import code_fingerprint, run_database_profiles
from prototypes.qwen_execution.testing import run_database_profiles_offline

pytest_plugins = ["tests.conftest"]  # Same socket prohibition and isolated test journal roots.


def admission_for(binding, tmp_path, **updates):
    fields = {
        "campaign_id": binding.budget.snapshot()["campaign_id"],
        "human_approval_reference": "offline-only-not-real-approval",
        "purpose": "database_live",
        "model": PRICE.model,
        "endpoint_sha256": sha256(binding.settings.base_url.encode()).hexdigest(),
        "source_fingerprints": {binding.source.fingerprint: binding.source.fingerprint},
        "price_fingerprint": PRICE.fingerprint,
        "valid_until": PRICE.valid_until,
        "max_requests": 6,
        "max_output_tokens": 800,
        "total_token_limit": 5_955_648,
        "cost_cap_cny": "4.78",
        "independent_review_reference": "offline-not-independent-review",
        "reviewed_code_sha256": code_fingerprint(),
    }
    fields.update(updates)
    path = tmp_path / "test-only-approval.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return live_admission.LiveAdmission.load(
        {
            live_admission.APPROVAL_ENV: str(path),
            live_admission.LEDGER_ROOT_ENV: str(tmp_path / "unused-live-ledger"),
        }
    )


@pytest.mark.parametrize("profile_binding", ["balanced72"], indirect=True)
def test_actual_pr98_gate_and_original_agent_are_compatible_offline(profile_binding, tmp_path):
    binding, service, _, _, _ = profile_binding
    admission = admission_for(binding, tmp_path)
    binding = replace(binding, require_externalization=admission.require)
    captured = []
    result, _, executor = asyncio.run(
        run_database_profiles_offline(
            binding,
            limits=service.profile_limits,
            transport=transport_for(captured),
        )
    )
    assert result["status"] == "completed" and result["profiles"]
    assert result["simulation"] and result["origin"] == result["budget"]["origin"] == "http_mock"
    assert result["actual_remote_requests"] == 0 and result["real_qwen"] == "not_run"
    assert len(captured) >= 2
    assert_database_profiles(binding, result, executor)
    assert not admission.ledger_root.exists()  # No simulated usage goes into a Live journal.
    with pytest.raises(AgentError, match="database_live_budget_not_installed"):
        asyncio.run(run_database_profiles(binding, limits=service.profile_limits))


@pytest.mark.parametrize("profile_binding", ["balanced72"], indirect=True)
def test_actual_pr98_unregistered_source_is_rejected_before_capture(
    profile_binding, tmp_path, monkeypatch
):
    binding, service, _, _, _ = profile_binding
    admission = admission_for(binding, tmp_path, source_fingerprints={"0" * 64: "0" * 64})
    binding = replace(binding, require_externalization=admission.require)
    monkeypatch.setattr(service.provider, "capture", lambda *_: pytest.fail("read before gate"))
    with pytest.raises(AgentError, match="source_not_registered_for_remote"):
        asyncio.run(
            run_database_profiles_offline(
                binding, limits=service.profile_limits, transport=transport_for([])
            )
        )
    assert binding.budget.snapshot()["request_count"] == 0
