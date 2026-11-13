"""Lifecycle failures against real SQLite and the existing HTTP Mock executor."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient
from prototypes.adaptive_assessment.live.budget import OfflineSnapshotPermit
from prototypes.adaptive_assessment.live.pricing import RESEARCH_PRICE_20261104 as PRICE
from prototypes.durable_teacher_agent.models import TaskCreate
from prototypes.durable_teacher_agent.service import DurableBudget, TaskService
from prototypes.durable_teacher_agent.store import TaskStore
from prototypes.research_agent_v2.runtime import ResearchExecutor
from prototypes.teacher_agent_bridge import api as bridge
from pydantic import SecretStr

from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.qwen import CallMetric
from app.ai.edu.qwen_config import QwenSettings
from app.core.errors import APIError

from .test_tasks import QUESTION, actor, setup, upload


@pytest.fixture
def pending(tmp_path):
    fixture, service = setup(tmp_path)
    with TestClient(fixture.app) as client:
        client.headers["Authorization"] = "Bearer " + fixture.token
        b = upload(client, fixture)
        a = actor(fixture, service)
        gid = service.store.grant(a, b["batch_id"], b["snapshot_id"])
        body = TaskCreate(
            snapshot_id=b["snapshot_id"], question=QUESTION, idempotency_key="pending-test-01"
        )
        row, fresh = service.store.create(a, b["batch_id"], body)
        assert fresh
        yield fixture, service, client, b, a, gid, body, row


def test_concurrent_requests_and_claim_are_singleton(pending):
    _, service, _, b, a, _, body, row = pending
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.store.create(a, b["batch_id"], body), range(4)))
    assert {r[0]["task_id"] for r in results} == {row["task_id"]}
    assert not any(r[1] for r in results)
    service.store.start(row["task_id"])
    with pytest.raises(APIError):
        service.store.start(row["task_id"])


def test_timeout_expiry_and_restart_do_not_resume(pending):
    _, service, _, _, a, _, _, row = pending
    service.store.start(row["task_id"])
    restarted = TaskStore(service.provider, clock=lambda: row["deadline"] + 1)
    current = restarted.get(a, row["task_id"])
    assert current["state"] == "timed_out" and current["result_json"] is None
    with pytest.raises(APIError):
        restarted.start(row["task_id"])
    expired = TaskStore(service.provider, clock=lambda: row["expires"] + 1)
    with pytest.raises(APIError) as error:
        expired.get(a, row["task_id"])
    assert error.value.status_code == 410


def test_cancelled_task_cannot_be_restarted(pending):
    _, service, client, b, a, _, body, row = pending
    path = f"/api/v1/edu/batches/{b['batch_id']}/ai-investigation-tasks/{row['task_id']}"
    response = client.post(path + "/cancel", params={"snapshot_id": b["snapshot_id"]})
    assert response.status_code == 200 and response.json()["state"] == "cancelled"
    TaskService(service.provider, TaskStore(service.provider), service.directory).execute(
        row["task_id"]
    )
    current = service.store.get(a, row["task_id"])
    assert current["state"] == "cancelled" and current["tool_attempts"] == 0
    with pytest.raises(APIError):
        service.store.create(
            a, b["batch_id"], body.model_copy(update={"idempotency_key": "cannot-refresh"})
        )


@pytest.mark.parametrize("action", ["cancel", "revoke_purpose", "revoke_credential"])
def test_inflight_failure_keeps_reservation_and_no_partial_result(pending, monkeypatch, action):
    fixture, service, client, b, _, gid, _, row = pending
    entered, release = Event(), Event()
    original = bridge.mock_transport

    def paused_transport(*args):
        base = original(*args)

        def handler(request):
            entered.set()
            if not release.wait(20):
                raise AssertionError("test_release_missing")
            return base.handle_request(request)

        return httpx.MockTransport(handler)

    monkeypatch.setattr(bridge, "mock_transport", paused_transport)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(service.execute, row["task_id"])
        try:
            assert entered.wait(30)
            if action == "cancel":
                path = (
                    f"/api/v1/edu/batches/{b['batch_id']}/ai-investigation-tasks/{row['task_id']}"
                )
                response = client.post(path + "/cancel", params={"snapshot_id": b["snapshot_id"]})
                assert response.status_code == 200
            elif action == "revoke_purpose":
                service.store.revoke(gid)
            else:
                fixture.admin.revoke_tokens(fixture.teacher_id)
        finally:
            release.set()
        task.result(timeout=30)
    with service.store.database.transaction() as c:
        current = service.store._row(c, row["task_id"])
    assert current["result_json"] is None
    budget = json.loads(current["budget_json"])
    assert budget["request_count"] == 1
    assert budget["entries"][0]["state"] == "reserved"
    assert current["tool_attempts"] == 1
    assert len(json.loads(current["disclosure_json"])) > 0


def test_durable_model_and_tool_caps_survive_new_objects(pending):
    _, service, _, b, a, _, body, row = pending
    row = service.store.start(row["task_id"])
    source = service.source(row, a, running=True)
    permit = OfflineSnapshotPermit(source, row["campaign_id"], PRICE.fingerprint)
    budget = DurableBudget(permit, service.directory, service.store, row["task_id"])
    settings = QwenSettings(
        SecretStr("synthetic-mock-placeholder"), "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    budget.claim(settings, source)
    try:
        for _ in range(6):
            ticket = budget.reserve()
            metric = CallMetric("plan", settings.model, 1, "ok", 200, 10, 10, 20, 0, 800)
            budget.settle(ticket, metric, failed=False)
        with pytest.raises(AgentError, match="authorized_request_limit"):
            budget.reserve()
    finally:
        budget.close()
    with pytest.raises(AgentError, match="campaign_already_consumed"):
        DurableBudget(permit, service.directory, service.store, row["task_id"])
    current = service.store.get(a, row["task_id"])
    assert json.loads(current["budget_json"])["request_count"] == 6
    restarted = TaskService(
        service.provider, TaskStore(service.provider), service.directory / "new-path"
    )
    with pytest.raises(APIError):
        restarted.store.create(
            a, b["batch_id"], body.model_copy(update={"idempotency_key": "fresh-campaign-id"})
        )
    executor = ResearchExecutor(source, "synthetic-budget-proof", Limits())
    for _ in range(5):
        service.store.audit(
            row["task_id"], "attempt", executor, {"tool": "invalid", "arguments": {}}
        )
    with pytest.raises(APIError) as error:
        service.store.audit(
            row["task_id"], "attempt", executor, {"tool": "invalid", "arguments": {}}
        )
    assert error.value.code == "AI_TOOL_BUDGET_EXHAUSTED"


@pytest.mark.parametrize(
    "case,question,expected",
    [
        ("evidence", "为什么 算法与数据结构 与 代码实现能力 相关", "needs_external_evidence"),
        ("empty", "识别值得优先核查的评分异常", "no_supported_findings"),
    ],
)
def test_insufficient_evidence_and_zero_findings(tmp_path, case, question, expected):
    fixture, service = setup(tmp_path)
    with TestClient(fixture.app) as client:
        client.headers["Authorization"] = "Bearer " + fixture.token
        b = upload(client, fixture, case)
        service.store.grant(actor(fixture, service), b["batch_id"], b["snapshot_id"])
        path = f"/api/v1/edu/batches/{b['batch_id']}/ai-investigation-tasks"
        response = client.post(
            path,
            json={
                "snapshot_id": b["snapshot_id"],
                "question": question,
                "idempotency_key": "safe-stop-" + case,
            },
        )
        assert response.status_code == 202
        response = client.get(
            path + "/" + response.json()["task_id"], params={"snapshot_id": b["snapshot_id"]}
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["stop_reason"] == expected
        assert result["real_qwen"] == "not_run"
        if case == "empty":
            assert result["selected_fact_ids"] == []
        else:
            assert result["missing_evidence"]


def test_no_purpose_and_no_client_authorization(tmp_path, monkeypatch):
    fixture, service = setup(tmp_path)
    with TestClient(fixture.app) as client:
        client.headers["Authorization"] = "Bearer " + fixture.token
        b = upload(client, fixture)
        path = f"/api/v1/edu/batches/{b['batch_id']}/ai-investigation-tasks"
        body = {
            "snapshot_id": b["snapshot_id"],
            "question": QUESTION,
            "idempotency_key": "no-purpose-01",
        }
        captured = []
        original = service.provider.capture

        def record_capture(*args):
            captured.append(True)
            return original(*args)

        monkeypatch.setattr(service.provider, "capture", record_capture)
        response = client.post(path, json=body)
        assert response.status_code == 403
        assert captured == []
        for field, value in (
            ("authorized", True),
            ("facts", []),
            ("statistics", {}),
            ("mode", "synthetic_live"),
        ):
            response = client.post(path, json=body | {field: value})
            assert response.status_code == 422
        assert not any("/ai-investigations" in getattr(r, "path", "") for r in fixture.app.routes)


def test_invalid_mock_response_never_publishes_partial_facts(pending, monkeypatch):
    _, service, _, _, a, _, _, row = pending
    monkeypatch.setattr(
        bridge,
        "mock_transport",
        lambda *args: httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": []})
        ),
    )
    service.execute(row["task_id"])
    current = service.store.get(a, row["task_id"])
    assert current["state"] == "failed" and current["result_json"] is None
    assert current["error_code"] == "AI_EXECUTION_INCOMPLETE"
    assert json.loads(current["budget_json"])["request_count"] == 1


def test_duplicate_worker_does_not_fail_the_running_owner(pending):
    _, service, _, _, a, _, _, row = pending
    service.store.start(row["task_id"])
    service.execute(row["task_id"])
    current = service.store.get(a, row["task_id"])
    assert current["state"] == "running"
    assert current["tool_attempts"] == 0


def test_status_read_does_not_take_write_lock(pending):
    """A status read must not contend with an execution write via a needless write transaction."""
    _, service, _, _, a, _, _, row = pending
    service.store.start(row["task_id"])
    # Simulate the execution worker holding the database write lock (BEGIN IMMEDIATE).
    with service.store.database.transaction(write=True):
        try:
            current = service.store.get(a, row["task_id"])
        except APIError as error:
            pytest.fail(
                f"status read raised HTTP {error.status_code} ({error.code}) under a "
                "concurrent write; it must use a read transaction instead of BEGIN IMMEDIATE"
            )
    assert current["state"] == "running"


def test_running_status_materializes_once_and_rechecks_purpose(pending, monkeypatch):
    _, service, client, batch, _, grant, _, row = pending
    service.store.start(row["task_id"])
    route = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks/{row['task_id']}"
    capture = service.provider.capture
    captures = 0
    revoke = False

    def counted_capture(*args):
        nonlocal captures
        captures += 1
        result = capture(*args)
        if revoke:
            service.store.revoke(grant)
        return result

    monkeypatch.setattr(service.provider, "capture", counted_capture)
    params = {"snapshot_id": batch["snapshot_id"]}
    response = client.get(route, params=params)
    assert response.status_code == 200
    assert response.json()["state"] == "running"
    assert response.json()["result"] is None
    assert captures == 1

    revoke = True
    response = client.get(route, params=params)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_PURPOSE_REVOKED"
    assert captures == 2
