"""New P0-C deny-before-admission cases; reuse #75 positives and #72 lifecycle tests."""

from fastapi.testclient import TestClient

from .test_tasks import actor, setup, upload


def test_prohibited_intents_never_create_task_or_start_worker(tmp_path, monkeypatch):
    fixture, service = setup(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("denied_goal_must_not_start_worker")

    monkeypatch.setattr(service, "execute", forbidden)
    try:
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            batch = upload(client, fixture)
            service.store.grant(actor(fixture, service), batch["batch_id"], batch["snapshot_id"])
            base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
            for index, question in enumerate(
                (
                    "优先核查所有学生的姓名和学号",
                    "核查并把I01的分数改为5分",
                    "证明I01导致I02",
                    "优先帮我写一首诗",
                )
            ):
                response = client.post(
                    base,
                    json={
                        "snapshot_id": batch["snapshot_id"],
                        "question": question,
                        "idempotency_key": f"denied-goal-{index}",
                    },
                )
                assert response.status_code == 422
                assert response.json()["error"]["code"] == "AI_GOAL_UNSUPPORTED"
            with service.store.database.transaction() as connection:
                assert (
                    connection.execute("SELECT count(*) FROM ai_synthetic_tasks").fetchone()[0] == 0
                )
                assert (
                    connection.execute("SELECT count(*) FROM ai_synthetic_events").fetchone()[0]
                    == 0
                )
            assert not list((tmp_path / "campaigns").rglob("*.jsonl"))
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)
