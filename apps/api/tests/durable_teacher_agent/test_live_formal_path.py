"""Formal FastAPI -> queued Live task -> worker -> verified readback/restart.

The network is intercepted, so this is a real code-path test, not a paid call.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import httpx
from fastapi.testclient import TestClient
from prototypes.adaptive_assessment.live.demo import MOCK_BASE, native_response
from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261125 as PRICE
from prototypes.capability_profiles.agent import ProfileMockPolicy
from prototypes.durable_teacher_agent import live_admission
from prototypes.durable_teacher_agent.service import TaskService
from prototypes.durable_teacher_agent.store import TaskStore
from prototypes.durable_teacher_agent.worker import TaskWorker
from prototypes.qwen_execution import code_fingerprint

from app.core.config import Settings
from app.main import create_app

from .test_database_profiles import profile_host
from .test_tasks import actor, upload

FINGERPRINT = "f" * 64


def _live_approval(campaign_id, source_sha):
    return {
        "campaign_id": campaign_id,
        "human_approval_reference": "owner-approval-offline-01",
        "purpose": "database_live",
        "model": PRICE.model,
        "endpoint_sha256": sha256(MOCK_BASE.encode()).hexdigest(),
        "source_fingerprints": {source_sha: source_sha},
        "price_fingerprint": PRICE.fingerprint,
        "valid_until": PRICE.valid_until,
        "max_requests": 6,
        "max_output_tokens": 800,
        "total_token_limit": 5_955_648,
        "cost_cap_cny": "4.78",
        "independent_review_reference": "offline-review-01",
        "reviewed_code_sha256": FINGERPRINT,
    }


def test_formal_live_path_create_execute_readback_concurrent_and_restart(tmp_path, monkeypatch):
    fixture, fixture_service = profile_host(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture, "balanced72")
    teacher = actor(fixture, fixture_service)
    with fixture.app.state.edu_service.database.transaction() as connection:
        file_sha = connection.execute(
            "SELECT file_sha256 FROM batches WHERE batch_id=?", (batch["batch_id"],)
        ).fetchone()["file_sha256"]

    real_approval = tmp_path / "real-data-approval.json"
    real_approval.write_text(
        json.dumps(
            {
                "purpose": "real_teacher_data",
                "human_approval_reference": "OWNER-REAL-01",
                "independent_review_reference": "REVIEW-REAL-01",
                "reviewed_code_sha256": code_fingerprint(),
                "valid_until": "2026-12-31",
                "sources": [
                    {
                        "source_sha256": file_sha,
                        "valid_until": "2026-12-31",
                        "human_approval_reference": "OWNER-REAL-01",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WEIRAN_EDU_REAL_DATA_APPROVAL", str(real_approval))
    monkeypatch.setenv("WEIRAN_EDU_AI_STATE_DIR", str(tmp_path / "ai-state"))
    application = create_app(
        Settings(
            environment="production",
            database_path=fixture.app.state.settings.database_path,
            ai_task_routes=False,
        )
    )
    service = application.state.ai_task_service
    assert service is not None and application.state.ai_task_mode == "qwen_live"

    service.store.grant_database_live(teacher, batch["batch_id"], batch["snapshot_id"])
    with service.store.database.transaction() as connection:
        live_source_sha = connection.execute(
            "SELECT source_sha FROM ai_synthetic_grants WHERE teacher_id=? "
            "AND batch_id=? AND snapshot_id=?",
            (teacher.teacher_id, batch["batch_id"], batch["snapshot_id"]),
        ).fetchone()["source_sha"]
    with TestClient(application, headers={"Authorization": "Bearer " + fixture.token}) as client:
        base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
        response = client.post(
            base,
            json={
                "snapshot_id": batch["snapshot_id"],
                "question": "有哪些值得关注的问题？",
                "idempotency_key": "formal-live-01",
            },
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        queued = client.get(f"{base}/{task_id}", params={"snapshot_id": batch["snapshot_id"]})
        assert queued.status_code == 200, queued.text
        assert queued.json()["state"] == "queued"
        campaign_id = queued.json()["campaign_id"]

        approval = tmp_path / "qwen-live-approval.json"
        approval.write_text(
            json.dumps(_live_approval(campaign_id, live_source_sha)), encoding="utf-8"
        )
        monkeypatch.setenv("WEIRAN_EDU_QWEN_LIVE_APPROVAL", str(approval))
        monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", str(tmp_path / "ledger"))
        monkeypatch.setenv("DASHSCOPE_BASE_URL", MOCK_BASE)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "offline-placeholder")
        monkeypatch.setattr(live_admission, "code_fingerprint", lambda: FINGERPRINT)

        calls = []

        def handler(request):
            payload = json.loads(request.content)
            calls.append(payload)
            view = json.loads(payload["messages"][-1]["content"])
            return httpx.Response(
                200, json=native_response(ProfileMockPolicy().choose(view), len(calls))
            )

        real_client = httpx.AsyncClient

        def intercepted(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", intercepted)

        assert TaskWorker(service).run_once() == 1
        response = client.get(f"{base}/{task_id}", params={"snapshot_id": batch["snapshot_id"]})
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["state"] == "completed", (view["state"], view["error_code"])
        assert view["execution_origin"] == "qwen_live"
        assert view["real_qwen"] == "completed"
        assert view["provenance"]["source_replay_verified"] is True
        assert calls
        assert view["result"]["facts"]
        assert all(fact["source_verified"] for fact in view["result"]["facts"])

        # Concurrent readback must not observe a completed-without-provenance state.
        def read():
            return service.view(
                teacher, batch["batch_id"], task_id, batch["snapshot_id"]
            ).model_dump(mode="json")

        with ThreadPoolExecutor(max_workers=4) as pool:
            reads = list(pool.map(lambda _: read(), range(8)))
        assert all(item == view for item in reads)

        profile = view["result"]["profile_projection"]["profiles"][0]
        scores = client.post(
            f"{base}/{task_id}/profiles/scores/query",
            json={
                "snapshot_id": batch["snapshot_id"],
                "profile_id": profile["profile_id"],
                "dimension_code": profile["dimensions"][0]["dimension_code"],
                "side": "target",
                "offset": 0,
                "limit": 10,
            },
        )
        assert scores.status_code == 200, scores.text
        assert scores.json()["detail_status"] in {"available", "privacy_limited"}

    restarted = TaskService(service.provider, TaskStore(service.provider), service.directory)
    reread = restarted.view(teacher, batch["batch_id"], task_id, batch["snapshot_id"])
    assert reread.model_dump(mode="json") == read()
    assert restarted.store.queued() == []
