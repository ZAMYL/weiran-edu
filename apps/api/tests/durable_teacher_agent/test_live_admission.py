"""The Live admission gate must fail closed and never reuse a campaign."""

import json
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

import pytest
from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261125 as PRICE
from prototypes.durable_teacher_agent import live_admission as admission_module
from prototypes.durable_teacher_agent.live_admission import (
    APPROVAL_ENV,
    LEDGER_ROOT_ENV,
    DatabaseLiveApproval,
    ExternalizationScope,
    LiveAdmission,
)

from app.ai.edu.discovery.models import AgentError

ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
SOURCE_SHA = "a" * 64
FINGERPRINT = "f" * 64


@pytest.fixture(autouse=True)
def fixed_code_fingerprint(monkeypatch):
    monkeypatch.setattr(admission_module, "code_fingerprint", lambda: FINGERPRINT)


def approval_fields(**overrides):
    fields = {
        "campaign_id": "live-campaign-01",
        "human_approval_reference": "approval-2026-11-16",
        "purpose": "database_live",
        "model": PRICE.model,
        "endpoint_sha256": sha256(ENDPOINT.encode()).hexdigest(),
        "source_fingerprints": {SOURCE_SHA: SOURCE_SHA},
        "price_fingerprint": PRICE.fingerprint,
        "valid_until": PRICE.valid_until,
        "max_requests": 6,
        "max_output_tokens": 800,
        "total_token_limit": 5_955_648,
        "cost_cap_cny": Decimal("4.78"),
        "independent_review_reference": "review-ref-2026-11-16",
        "reviewed_code_sha256": FINGERPRINT,
    }
    fields.update(overrides)
    return fields


def approval(**overrides):
    return DatabaseLiveApproval.model_validate(approval_fields(**overrides))


def scope(**overrides):
    values = {
        "teacher_id": "teacher-1",
        "task_id": "task-1",
        "campaign_id": "live-campaign-01",
        "batch_id": "batch-1",
        "snapshot_id": "snapshot-1",
        "source_sha256": SOURCE_SHA,
        "model": PRICE.model,
        "endpoint": ENDPOINT,
    }
    values.update(overrides)
    return ExternalizationScope(**values)


def install(tmp_path, **overrides):
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval_fields(**overrides), default=str), encoding="utf-8")
    return {APPROVAL_ENV: str(path), LEDGER_ROOT_ENV: str(tmp_path / "ledger")}


def test_missing_approval_environment_fails_closed(tmp_path):
    with pytest.raises(AgentError, match="live_admission_not_configured"):
        LiveAdmission.load({LEDGER_ROOT_ENV: str(tmp_path / "ledger")})


def test_approval_inside_the_repository_is_rejected(tmp_path):
    repository = admission_module.REPOSITORY
    env = install(tmp_path)
    env[APPROVAL_ENV] = str(repository / "approval.json")
    with pytest.raises(AgentError, match="live_approval_must_stay_outside_repository"):
        LiveAdmission.load(env)


def test_expired_approval_is_rejected(tmp_path):
    env = install(tmp_path)
    tomorrow = date.today() + timedelta(days=400)
    with pytest.raises(AgentError, match="stale_authorization_or_price"):
        LiveAdmission.load(env, today=tomorrow)


def test_approval_for_different_code_is_rejected(tmp_path):
    env = install(tmp_path, reviewed_code_sha256="0" * 64)
    with pytest.raises(AgentError, match="reviewed_code_changed"):
        LiveAdmission.load(env)


def test_missing_or_relative_ledger_root_is_rejected(tmp_path):
    env = install(tmp_path)
    del env[LEDGER_ROOT_ENV]
    with pytest.raises(AgentError, match="campaign_ledger_root_not_configured"):
        LiveAdmission.load(env)
    env[LEDGER_ROOT_ENV] = "relative/ledger"
    with pytest.raises(AgentError, match="campaign_ledger_root_must_be_absolute"):
        LiveAdmission.load(env)


def test_ledger_root_inside_the_repository_is_rejected(tmp_path):
    env = install(tmp_path)
    env[LEDGER_ROOT_ENV] = str(admission_module.REPOSITORY / "ledger")
    with pytest.raises(AgentError, match="campaign_ledger_root_must_stay_outside_repository"):
        LiveAdmission.load(env)


def test_gate_rejects_any_scope_that_does_not_match(tmp_path):
    admission = LiveAdmission.load(install(tmp_path))
    admission.require(scope())
    for overrides, code in (
        ({"purpose": "synthetic_live"}, "remote_processing_purpose_rejected"),
        ({"model": "qwen-other"}, "remote_processing_model_mismatch"),
        ({"endpoint": "https://example.invalid"}, "remote_processing_endpoint_mismatch"),
        ({"source_sha256": "b" * 64}, "source_not_registered_for_remote"),
    ):
        with pytest.raises(AgentError, match=code):
            admission.require(scope(**overrides))


def test_budget_is_bound_to_the_server_ledger_and_is_single_use(tmp_path):
    admission = LiveAdmission.load(install(tmp_path))
    source = SimpleNamespace(recipe="authorized-db-batch", fingerprint=SOURCE_SHA)
    first = admission.budget(object(), "task-1", source)
    try:
        assert first.origin == "qwen_live"
        assert first.snapshot()["campaign_id"] == "live-campaign-01"
        journal = admission.ledger_root / "live-campaign-01.jsonl"
        assert journal.is_file()
        with pytest.raises(AgentError, match="campaign_already_consumed"):
            admission.budget(object(), "task-2", source)
    finally:
        first.close()


def test_live_budget_rejects_a_caller_supplied_directory(tmp_path):
    admission = LiveAdmission.load(install(tmp_path))
    source = SimpleNamespace(recipe="authorized-db-batch", fingerprint=SOURCE_SHA)
    budget = admission.budget(object(), "task-1", source)
    with pytest.raises(AgentError, match="live_ledger_root_is_server_owned"):
        budget._journal_directory(tmp_path / "elsewhere")
    budget.close()
