"""The durable worker claims queued tasks without the HTTP background shortcut."""

from fastapi.testclient import TestClient
from prototypes.durable_teacher_agent.models import TaskCreate
from prototypes.durable_teacher_agent.worker import TaskWorker

from .test_database_profiles import profile_host
from .test_tasks import actor, upload


def test_worker_claims_and_completes_a_queued_task(tmp_path):
    fixture, service = profile_host(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture, "balanced72")
    teacher = actor(fixture, service)
    service.store.grant(teacher, batch["batch_id"], batch["snapshot_id"])
    task, _ = service.store.create(
        teacher,
        batch["batch_id"],
        TaskCreate(
            snapshot_id=batch["snapshot_id"],
            question="有哪些值得关注的问题？",
            idempotency_key="worker-once-01",
        ),
    )

    worker = TaskWorker(service, interval=0.1)
    assert worker.run_once() == 1
    row = service.store.get(teacher, task["task_id"])
    assert row["state"] == "completed"
    assert row["error_code"] is None
    assert worker.run_once() == 0


def test_worker_does_not_pick_an_expired_task(tmp_path):
    fixture, service = profile_host(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture, "balanced72")
    teacher = actor(fixture, service)
    service.store.grant(teacher, batch["batch_id"], batch["snapshot_id"])
    task, _ = service.store.create(
        teacher,
        batch["batch_id"],
        TaskCreate(
            snapshot_id=batch["snapshot_id"],
            question="有哪些值得关注的问题？",
            idempotency_key="worker-expired-01",
        ),
        timeout=1,
    )
    with service.store.database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE ai_synthetic_tasks SET deadline=? WHERE task_id=?",
            (service.store.clock() - 1, task["task_id"]),
        )
    assert TaskWorker(service).run_once() == 0
    row = service.store.get(teacher, task["task_id"])
    assert row["state"] == "timed_out"
