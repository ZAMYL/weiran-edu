"""Real synthetic XLSX/SQLite task binding; no remote consent or model invocation."""

import asyncio
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from prototypes.adaptive_assessment.live.budget import OfflineSnapshotPermit
from prototypes.adaptive_assessment.live.pricing import RESEARCH_PRICE_20261104
from prototypes.durable_teacher_agent.models import TaskCreate
from prototypes.durable_teacher_agent.service import DurableBudget
from prototypes.qwen_execution import (
    DatabaseTaskInput,
    ExecutionProvenance,
    run_database_profiles,
)
from pydantic import SecretStr, ValidationError

from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.qwen_config import QwenSettings
from app.core.errors import APIError

from .test_tasks import actor, setup, upload


def missing_consent(_scope):
    raise AgentError("remote_processing_not_authorized")


@pytest.fixture
def bound(tmp_path):
    fixture, service = setup(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture)
    principal = actor(fixture, service)
    grant = service.store.grant(principal, batch["batch_id"], batch["snapshot_id"])
    row, _ = service.store.create(
        principal,
        batch["batch_id"],
        TaskCreate(
            snapshot_id=batch["snapshot_id"],
            question="有哪些值得关注的问题？",
            idempotency_key="contract-binding-01",
        ),
    )
    row = service.store.start(row["task_id"])
    source = service.source(row, principal, running=True)
    permit = OfflineSnapshotPermit(source, row["campaign_id"], RESEARCH_PRICE_20261104.fingerprint)
    budget = DurableBudget(permit, tmp_path / "campaigns", service.store, row["task_id"])
    request = DatabaseTaskInput(
        row["task_id"],
        batch["batch_id"],
        batch["snapshot_id"],
        principal,
        service.store,
        source,
        budget,
        QwenSettings(
            SecretStr("synthetic-test-only"), "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        missing_consent,
    )
    try:
        yield request, service, grant
    finally:
        budget.close()
        fixture.admin.revoke_tokens(fixture.teacher_id)


def test_database_read_authority_does_not_grant_externalization(bound, monkeypatch):
    request, service, _ = bound
    monkeypatch.setattr(service.provider, "capture", lambda *_: pytest.fail("read before consent"))
    before = request.budget.snapshot()
    with pytest.raises(AgentError, match="remote_processing_not_authorized"):
        request.preflight()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: pytest.fail("HTTP before consent"))
    with pytest.raises(AgentError, match="remote_processing_not_authorized"):
        asyncio.run(run_database_profiles(request, limits=Limits()))
    assert request.budget.snapshot() == before


def test_even_test_gate_cannot_upgrade_existing_mock_budget(bound):
    request, _, _ = bound
    scopes = []
    admitted = replace(request, require_externalization=lambda scope: scopes.append(scope))
    with pytest.raises(AgentError, match="database_live_budget_not_installed"):
        admitted.preflight()
    assert len(scopes) == 2 and scopes[0] == scopes[1]
    assert scopes[0].purpose == "assessment_aggregate_remote_inference"
    assert request.budget.snapshot()["request_count"] == 0
    assert not request.budget.snapshot()["claimed"]


@pytest.mark.parametrize("change", [{"batch_id": "other"}, {"snapshot_id": "other"}])
def test_cross_task_snapshot_binding_fails_before_remote_gate(bound, change):
    request, _, _ = bound
    request = replace(
        request, **change, require_externalization=lambda _: pytest.fail("wrong scope")
    )
    with pytest.raises(AgentError, match="database_task_binding_conflict"):
        request.preflight()


def test_revocation_during_database_validation_is_not_cached(bound, monkeypatch):
    request, service, grant = bound
    original = service.provider.capture

    def revoke_during_read(*args):
        value = original(*args)
        service.store.revoke(grant)
        return value

    monkeypatch.setattr(service.provider, "capture", revoke_during_read)
    with pytest.raises(APIError):
        replace(request, require_externalization=lambda _: None).preflight()
    assert request.budget.snapshot()["request_count"] == 0


def test_forged_synthetic_flag_is_not_a_database_source(bound):
    request, _, _ = bound
    with pytest.raises(AgentError, match="trusted_database_task_required"):
        replace(request, source={"synthetic_only": True}).preflight()


@pytest.mark.parametrize("origin", ["qwen_live", "http_mock"])
def test_provenance_keeps_real_and_mock_separate(origin):
    value = ExecutionProvenance(
        origin=origin,
        state="completed",
        request_state="responses_received",
        model_requests=2,
        tool_attempts=2,
        source_replay_verified=True,
    )
    assert value.origin == origin


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "not_run"},
        {"request_state": "not_run"},
        {"source_replay_verified": False},
        {"state": "not_run"},
        {"state": "failed"},
    ],
)
def test_provenance_rejects_contradictory_completion(change):
    fields = dict(
        origin="qwen_live",
        state="completed",
        request_state="responses_received",
        model_requests=2,
        tool_attempts=2,
        source_replay_verified=True,
    )
    with pytest.raises(ValidationError):
        ExecutionProvenance(**(fields | change))
