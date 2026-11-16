"""The production router enqueues; the separate worker claims and completes."""

import json

from fastapi.testclient import TestClient
from prototypes.durable_teacher_agent.api import install_production
from prototypes.durable_teacher_agent.worker import TaskWorker

from app.core.config import Settings
from app.main import create_app
from app.production import build_production

from .test_database_profiles import profile_host
from .test_tasks import actor, upload


def test_production_router_enqueues_and_worker_completes(tmp_path):
    fixture, service = profile_host(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture, "balanced72")
    teacher = actor(fixture, service)
    service.store.grant(teacher, batch["batch_id"], batch["snapshot_id"])

    application = create_app(
        Settings(
            environment="production",
            database_path=fixture.app.state.settings.database_path,
            ai_task_routes=False,
        )
    )
    install_production(application, service)
    with TestClient(application, headers={"Authorization": "Bearer " + fixture.token}) as client:
        base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
        response = client.post(
            base,
            json={
                "snapshot_id": batch["snapshot_id"],
                "question": "有哪些值得关注的问题？",
                "idempotency_key": "production-enqueue-01",
            },
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        status = client.get(f"{base}/{task_id}", params={"snapshot_id": batch["snapshot_id"]})
        assert status.status_code == 200, status.text
        assert status.json()["state"] == "queued"

        assert TaskWorker(service).run_once() == 1
        status = client.get(f"{base}/{task_id}", params={"snapshot_id": batch["snapshot_id"]})
        assert status.status_code == 200, status.text
        assert status.json()["state"] == "completed"


def test_production_factory_installs_the_real_router(tmp_path, monkeypatch):
    fixture, _ = profile_host(tmp_path)
    approval = tmp_path / "real-data-approval.json"
    approval.write_text(
        json.dumps(
            {
                "purpose": "real_teacher_data",
                "human_approval_reference": "OWNER-REAL-01",
                "independent_review_reference": "REVIEW-REAL-01",
                "reviewed_code_sha256": "a" * 64,
                "valid_until": "2026-12-31",
                "sources": [
                    {
                        "source_sha256": "b" * 64,
                        "valid_until": "2026-12-31",
                        "human_approval_reference": "OWNER-REAL-01",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WEIRAN_EDU_DATABASE", str(fixture.app.state.settings.database_path))
    monkeypatch.setenv("WEIRAN_EDU_REAL_DATA_APPROVAL", str(approval))
    monkeypatch.setenv("WEIRAN_EDU_AI_STATE_DIR", str(tmp_path / "ai-state"))
    application, service = build_production()
    paths = application.openapi()["paths"]
    assert "/api/v1/edu/batches/{batch_id}/ai-investigation-tasks" in paths
    assert application.state.ai_task_mode == "qwen_live"
    assert application.state.ai_task_service is service
