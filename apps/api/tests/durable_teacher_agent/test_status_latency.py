"""Status reads must not repeat source materialization they already performed."""

import pytest
from fastapi.testclient import TestClient

from app.core.errors import APIError

from .test_tasks import QUESTION, actor, setup, upload


def prepare(tmp_path):
    fixture, service = setup(tmp_path)
    client = TestClient(fixture.app)
    client.headers["Authorization"] = "Bearer " + fixture.token
    batch = upload(client, fixture)
    grant_id = service.store.grant(actor(fixture, service), batch["batch_id"], batch["snapshot_id"])
    return fixture, service, client, batch, grant_id


def completed_task(client, batch, key="status-latency-01"):
    base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
    started = client.post(
        base,
        json={
            "snapshot_id": batch["snapshot_id"],
            "question": QUESTION,
            "idempotency_key": key,
        },
    )
    path = base + "/" + started.json()["task_id"]
    view = client.get(path, params={"snapshot_id": batch["snapshot_id"]}).json()
    assert view["state"] == "completed"
    return path, view


def test_completed_status_read_materializes_the_source_twice(tmp_path, monkeypatch):
    fixture, service, client, batch, _ = prepare(tmp_path)
    path, _ = completed_task(client, batch)
    original = service.provider.capture
    calls = []

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(service.provider, "capture", counted)
    response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
    assert response.status_code == 200
    # One materialization for the reply, one closing re-check of the whole source.
    assert len(calls) == 2


def test_completed_status_read_still_rejects_a_source_change_in_the_phase(tmp_path, monkeypatch):
    fixture, service, client, batch, _ = prepare(tmp_path)
    path, _ = completed_task(client, batch, key="status-latency-02")
    original = service.provider.capture
    calls = []

    def tampering(*args):
        material, manifest = original(*args)
        calls.append(1)
        if len(calls) >= 2:
            return material, manifest + b"tampered"
        return material, manifest

    monkeypatch.setattr(service.provider, "capture", tampering)
    response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"


def test_completed_status_read_still_rejects_tampered_result(tmp_path):
    fixture, service, client, batch, _ = prepare(tmp_path)
    path, view = completed_task(client, batch, key="status-latency-03")
    with service.store.database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE ai_synthetic_tasks SET result_json=? WHERE task_id=?",
            ("{}", view["task_id"]),
        )
    response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"


def test_completed_status_read_still_requires_an_active_grant(tmp_path):
    fixture, service, client, batch, grant_id = prepare(tmp_path)
    path, _ = completed_task(client, batch, key="status-latency-04")
    service.store.revoke(grant_id)
    response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_PURPOSE_REVOKED"


def test_prevalidated_scope_still_runs_the_final_check(tmp_path):
    fixture, service, client, batch, _ = prepare(tmp_path)
    _, view = completed_task(client, batch, key="status-latency-05")
    row = service.store.get(actor(fixture, service), view["task_id"])
    original = service.provider.capture
    calls = []

    def tampering(*args):
        material, manifest = original(*args)
        calls.append(1)
        if len(calls) >= 2:
            return material, manifest + b"late"
        return material, manifest

    service.provider.capture = tampering
    with pytest.raises(APIError) as error:
        source = service.source(row, actor(fixture, service))
        with source.validation_scope(prevalidated=True):
            pass
    assert error.value.code == "DATA_INTEGRITY_ERROR"
