"""Operators can issue a database_live grant; browsers cannot choose it."""

import pytest
from fastapi.testclient import TestClient
from prototypes.durable_teacher_agent.models import TaskCreate

from app.core.errors import APIError

from .test_tasks import actor, setup, upload


def test_operator_can_issue_a_database_live_grant(tmp_path):
    fixture, service = setup(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture)
    teacher = actor(fixture, service)
    grant_id = service.store.grant_database_live(teacher, batch["batch_id"], batch["snapshot_id"])

    with service.store.database.transaction() as connection:
        row = connection.execute(
            "SELECT purpose FROM ai_synthetic_grants WHERE grant_id=?", (grant_id,)
        ).fetchone()
    assert row["purpose"] == "database_live"

    task, fresh = service.store.create(
        teacher,
        batch["batch_id"],
        TaskCreate(
            snapshot_id=batch["snapshot_id"],
            question="有哪些值得关注的问题？",
            idempotency_key="live-grant-task-01",
        ),
    )
    assert fresh is True
    assert service.store.purpose(task["task_id"]) == "database_live"


def test_grant_purpose_is_not_silently_upgraded(tmp_path):
    fixture, service = setup(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture)
    teacher = actor(fixture, service)
    service.store.grant(teacher, batch["batch_id"], batch["snapshot_id"])
    with pytest.raises(APIError) as error:
        service.store.grant_database_live(teacher, batch["batch_id"], batch["snapshot_id"])
    assert error.value.code == "AI_GRANT_PURPOSE_CONFLICT"


def test_unknown_grant_purpose_is_rejected(tmp_path):
    fixture, service = setup(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture)
    teacher = actor(fixture, service)
    with pytest.raises(ValueError, match="invalid_ai_grant_purpose"):
        service.store.grant(
            teacher,
            batch["batch_id"],
            batch["snapshot_id"],
            purpose="client_chosen_live",
        )
