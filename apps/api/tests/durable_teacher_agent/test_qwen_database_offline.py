"""Formal synthetic XLSX -> SQLite -> original Agent/ledger -> verified Mock profiles."""

import asyncio
import json
from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from prototypes.adaptive_assessment.live.budget import OfflineSnapshotPermit
from prototypes.adaptive_assessment.live.demo import native_response
from prototypes.adaptive_assessment.live.pricing import RESEARCH_PRICE_20261104
from prototypes.capability_profiles.agent import ProfileMockPolicy
from prototypes.durable_teacher_agent.models import TaskCreate
from prototypes.durable_teacher_agent.service import DurableBudget
from prototypes.qwen_execution import DatabaseTaskInput, run_database_profiles
from prototypes.qwen_execution.testing import run_database_profiles_offline
from pydantic import SecretStr

from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.qwen_config import QwenSettings
from app.core.errors import APIError
from app.importing.edu.types import DIMENSIONS

from .test_database_profiles import profile_host
from .test_qwen_task_contract import bound as bound
from .test_tasks import actor, upload


@pytest.fixture(params=("balanced72", "zero81"))
def profile_binding(tmp_path, request):
    fixture, service = profile_host(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture, request.param)
    principal = actor(fixture, service)
    service.store.grant(principal, batch["batch_id"], batch["snapshot_id"])
    row, _ = service.store.create(
        principal,
        batch["batch_id"],
        TaskCreate(
            snapshot_id=batch["snapshot_id"],
            question="有哪些值得关注的问题？",
            idempotency_key="offline-db-once-01",
        ),
    )
    row = service.store.start(row["task_id"])
    source = service.source(row, principal, running=True)
    permit = OfflineSnapshotPermit(source, row["campaign_id"], RESEARCH_PRICE_20261104.fingerprint)
    budget = DurableBudget(permit, tmp_path / "mock-ledger", service.store, row["task_id"])
    scopes = []

    def synthetic_test_gate(scope):
        assert scope.source_sha256 == source.fingerprint
        assert scope.task_id == row["task_id"] and scope.teacher_id == principal.teacher_id
        scopes.append(scope)

    binding = DatabaseTaskInput(
        row["task_id"],
        batch["batch_id"],
        batch["snapshot_id"],
        principal,
        service.store,
        source,
        budget,
        QwenSettings(
            SecretStr("offline-synthetic-only"), "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        synthetic_test_gate,
    )
    try:
        yield binding, service, fixture, scopes, request.param
    finally:
        budget.close()
        fixture.admin.revoke_tokens(fixture.teacher_id)


def transport_for(captured, *, during_response=None):
    policy = ProfileMockPolicy()  # Existing offline selector, not a second Agent.

    def respond(request):
        payload = json.loads(request.content)
        captured.append(payload)
        if during_response is not None:
            during_response()
        view = json.loads(payload["messages"][-1]["content"])
        return httpx.Response(200, json=native_response(policy.choose(view), len(captured)))

    return httpx.MockTransport(respond)


def assert_database_profiles(binding, result, executor):
    with binding.store.database.transaction() as connection:
        rows = connection.execute(
            "SELECT s.public_id,e.dimension_code,e.score_units,e.evidence_id "
            "FROM evaluations e JOIN students s USING(student_pk) WHERE e.batch_id=?",
            (binding.batch_id,),
        ).fetchall()
    scores, evidence = {}, {}
    for row in rows:
        scores.setdefault(row["public_id"], {})[row["dimension_code"]] = Fraction(
            row["score_units"], 100
        )
        evidence[row["public_id"], row["dimension_code"]] = row["evidence_id"]
    for profile in result["profiles"]:
        assert profile["selection_origin"] == "http_mock"
        target = {
            pid
            for pid, values in scores.items()
            if all(
                {
                    "<": values[c["dimension_code"]] < Fraction(c["value"]),
                    "<=": values[c["dimension_code"]] <= Fraction(c["value"]),
                    "==": values[c["dimension_code"]] == Fraction(c["value"]),
                    ">=": values[c["dimension_code"]] >= Fraction(c["value"]),
                    ">": values[c["dimension_code"]] > Fraction(c["value"]),
                }[c["operator"]]
                for c in profile["conditions"]
            )
        }
        remainder = set(scores) - target
        assert profile["metrics"]["target_count"] == len(target)
        assert profile["metrics"]["remainder_count"] == len(remainder)
        for dimension in profile["dimensions"]:
            code = dimension["dimension_code"]
            for side, members in (("target", target), ("remainder", remainder)):
                expected = sum(scores[pid][code] for pid in members) / len(members)
                actual = dimension[side]["mean"]
                assert Fraction(actual["numerator"], actual["denominator"]) == expected
        for fid in profile["verified_fact_ids"]:
            fact = executor.facts[fid]
            outcome = fact.calculation.values["outcome"]
            code = DIMENSIONS[int(outcome[1:]) - 1].code
            witnesses = executor.resolve(fid, "target.valid")
            assert {w.participant_id for w in witnesses} == target
            assert {w.reference_id for w in witnesses} == {evidence[pid, code] for pid in target}
            assert all(w.source_sha256 == binding.source.fingerprint for w in witnesses)


def test_database_pipeline_uses_real_tools_but_cannot_claim_live_success(profile_binding):
    binding, service, _, scopes, case = profile_binding
    captured = []
    result, _, executor = asyncio.run(
        run_database_profiles_offline(
            binding,
            limits=service.profile_limits,
            transport=transport_for(captured),
        )
    )
    assert result["status"] == "completed", result["stop_reason"]
    assert result["origin"] == result["budget"]["origin"] == "http_mock"
    assert result["simulation"] and result["execution_mode"] == "offline_http_mock"
    assert result["real_qwen"] == "not_run" and result["actual_remote_requests"] == 0
    assert result["actual_tokens"] is None and result["actual_cost_cny"] is None
    assert result["source_replay_verified"] and not result["production_enabled"]
    assert 1 <= result["model_requests"] == len(captured) <= 6
    assert result["tool_attempts"] <= 5
    assert len(scopes) > 2 * len(captured)
    if case == "balanced72":
        assert len(captured) >= 2 and result["profiles"]
    else:
        assert result["profiles"] == []  # A legitimate zero-profile result, not a template.
    assert_database_profiles(binding, result, executor)
    row = binding.store.get(binding.actor, binding.task_id)
    assert row["tool_attempts"] == result["tool_attempts"]
    assert json.loads(row["budget_json"])["origin"] == "http_mock"
    assert json.loads(row["budget_json"])["request_count"] == len(captured)
    raw = json.dumps(captured)
    for forbidden in ("participants", "reference_id", "evidence_id", "source_sha256"):
        assert forbidden not in raw
    before = binding.budget.snapshot()
    with pytest.raises(AgentError, match="campaign_run_already_claimed"):
        asyncio.run(
            run_database_profiles_offline(
                binding, limits=service.profile_limits, transport=transport_for([])
            )
        )
    assert binding.budget.snapshot() == before
    with pytest.raises(AgentError, match="database_live_budget_not_installed"):
        asyncio.run(run_database_profiles(binding, limits=service.profile_limits))


def test_simulation_does_not_bypass_externalization_gate(bound, monkeypatch):
    binding, service, _ = bound
    monkeypatch.setattr(service.provider, "capture", lambda *_: pytest.fail("read before consent"))
    with pytest.raises(AgentError, match="remote_processing_not_authorized"):
        asyncio.run(
            run_database_profiles_offline(binding, limits=Limits(), transport=transport_for([]))
        )
    assert binding.budget.snapshot()["request_count"] == 0


@pytest.mark.parametrize(
    "transport",
    [None, object(), type("CustomMock", (httpx.MockTransport,), {})(lambda _: httpx.Response(200))],
)
def test_only_exact_mock_transport_is_accepted(bound, transport):
    binding, _, _ = bound
    with pytest.raises(AgentError, match="database_test_mock_transport_required"):
        asyncio.run(run_database_profiles_offline(binding, limits=Limits(), transport=transport))
    assert binding.budget.snapshot()["request_count"] == 0


def test_no_live_budget_or_json_capability_or_environment_switch(bound, monkeypatch):
    binding, _, _ = bound
    live = replace(binding, budget=SimpleNamespace(origin="qwen_live"))
    with pytest.raises(AgentError, match="database_test_mock_budget_required"):
        asyncio.run(
            run_database_profiles_offline(live, limits=Limits(), transport=transport_for([]))
        )
    with pytest.raises(AgentError, match="database_test_capability_required"):
        binding.preflight(_testing={"transport": "mock"})
    monkeypatch.setenv("WEIRAN_EDU_QWEN_OFFLINE", "1")
    admitted = replace(binding, require_externalization=lambda _: None)
    with pytest.raises(AgentError, match="database_live_budget_not_installed"):
        asyncio.run(run_database_profiles(admitted, limits=Limits()))
    with pytest.raises(TypeError):
        asyncio.run(run_database_profiles(admitted, limits=Limits(), transport=transport_for([])))
    assert binding.budget.snapshot()["request_count"] == 0


@pytest.mark.parametrize("profile_binding", ["balanced72"], indirect=True)
def test_browser_request_cannot_select_test_execution(profile_binding):
    binding, _, fixture, _, _ = profile_binding
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        for field in ("transport", "simulation", "execution_mode", "_testing", "allow_network"):
            response = client.post(
                f"/api/v1/edu/batches/{binding.batch_id}/ai-investigation-tasks",
                json={
                    "snapshot_id": binding.snapshot_id,
                    "question": "有哪些值得关注的问题？",
                    "idempotency_key": "browser-reject-01",
                    field: True,
                },
            )
            assert response.status_code == 422
    assert binding.budget.snapshot()["request_count"] == 0
    with binding.store.database.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM ai_synthetic_tasks").fetchone()[0] == 1


def test_revocation_during_response_fails_closed(bound):
    binding, service, grant = bound
    admitted = replace(binding, require_externalization=lambda _: None)
    captured = []
    transport = transport_for(captured, during_response=lambda: service.store.revoke(grant))
    with pytest.raises(APIError):
        asyncio.run(
            run_database_profiles_offline(
                admitted, limits=service.profile_limits, transport=transport
            )
        )
    assert len(captured) == binding.budget.snapshot()["request_count"] == 1
    assert binding.budget.snapshot()["entries"][0]["state"] == "reserved"


def test_database_model_budget_exhaustion_cannot_publish_or_restart(bound):
    binding, _, _ = bound
    binding = replace(binding, require_externalization=lambda _: None)
    captured = []
    result, _, _ = asyncio.run(
        run_database_profiles_offline(
            binding,
            limits=Limits(model_requests=1, timeout_seconds=30),
            transport=transport_for(captured),
        )
    )
    assert result["stop_reason"] == "model_budget_exhausted"
    assert result["status"] != "completed" and result["profiles"] == []
    assert result["simulation"] and result["real_qwen"] == "not_run"
    assert len(captured) == binding.budget.snapshot()["request_count"] == 1
    with pytest.raises(AgentError, match="campaign_run_already_claimed"):
        asyncio.run(
            run_database_profiles_offline(binding, limits=Limits(), transport=transport_for([]))
        )
    assert binding.budget.snapshot()["request_count"] == 1


def test_database_seed_tool_budget_exhaustion_never_requests_model(bound):
    binding, _, _ = bound
    binding = replace(binding, require_externalization=lambda _: None)
    with pytest.raises(AgentError, match="seed_budget_exhausted"):
        asyncio.run(
            run_database_profiles_offline(
                binding,
                limits=Limits(tool_attempts=1, timeout_seconds=30),
                transport=transport_for([]),
            )
        )
    assert binding.budget.snapshot()["request_count"] == 0


def test_database_change_during_simulation_is_rejected(bound):
    binding, service, _ = bound
    admitted = replace(binding, require_externalization=lambda _: None)

    with binding.store.database.transaction() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='immutable_evaluations_update'"
        ).fetchone()[0]
        original = connection.execute(
            "SELECT evidence_id,score_units FROM evaluations "
            "WHERE batch_id=? AND score_units<400 LIMIT 1",
            (binding.batch_id,),
        ).fetchone()

    def change_score():
        with binding.store.database.transaction(write=True) as connection:
            # Privileged, temporary SQLite corruption test; no production migration.
            connection.execute("DROP TRIGGER immutable_evaluations_update")
            connection.execute(
                "UPDATE evaluations SET score_units=? WHERE evidence_id=?",
                (original["score_units"] + 1, original["evidence_id"]),
            )
            connection.execute(trigger)

    try:
        with pytest.raises(APIError) as rejected:
            asyncio.run(
                run_database_profiles_offline(
                    admitted,
                    limits=service.profile_limits,
                    transport=transport_for([], during_response=change_score),
                )
            )
        assert rejected.value.status_code == 503
        assert rejected.value.code == "DATA_INTEGRITY_ERROR"
    finally:
        with binding.store.database.transaction(write=True) as connection:
            connection.execute("DROP TRIGGER immutable_evaluations_update")
            connection.execute(
                "UPDATE evaluations SET score_units=? WHERE evidence_id=?",
                (original["score_units"], original["evidence_id"]),
            )
            connection.execute(trigger)
    binding.source.validate()
