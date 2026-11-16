"""A database_live task must refuse without an admission and never touch the network."""

import httpx
from fastapi.testclient import TestClient
from prototypes.durable_teacher_agent.models import TaskCreate

from app.ai.edu.qwen_config import QwenSettings

from .test_tasks import QUESTION, actor, setup, upload


def live_task(tmp_path, monkeypatch):
    fixture, service = setup(tmp_path)
    client = TestClient(fixture.app)
    client.headers["Authorization"] = "Bearer " + fixture.token
    batch = upload(client, fixture)
    teacher = actor(fixture, service)
    service.store.grant(teacher, batch["batch_id"], batch["snapshot_id"])
    with service.store.database.transaction(write=True) as connection:
        connection.execute("UPDATE ai_synthetic_grants SET purpose='database_live'")
    body = TaskCreate(
        snapshot_id=batch["snapshot_id"], question=QUESTION, idempotency_key="live-admission-01"
    )
    row, _ = service.store.create(teacher, batch["batch_id"], body)
    return fixture, service, teacher, row


def test_live_task_without_admission_fails_closed(tmp_path, monkeypatch):
    fixture, service, teacher, row = live_task(tmp_path, monkeypatch)
    monkeypatch.delenv("WEIRAN_EDU_QWEN_LIVE_APPROVAL", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("an unapproved live task must not read credentials or open HTTP")

    monkeypatch.setattr(QwenSettings, "from_env", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)

    assert service.store.purpose(row["task_id"]) == "database_live"
    service.execute(row["task_id"])

    stored = service.store.get(teacher, row["task_id"])
    assert stored["state"] == "failed"
    assert stored["error_code"] == "AI_LIVE_ADMISSION_REQUIRED"
    assert stored["result_json"] is None and stored["provenance_json"] == "{}"
