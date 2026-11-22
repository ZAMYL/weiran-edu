"""LIVE-02：正式任务行上的真实 Qwen 接线、Worker 认领与失败封闭。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261119
from prototypes.qwen_execution.runtime import code_fingerprint
from prototypes.research_agent_v2.programming import INSPECT, OVERVIEW
from prototypes.research_agent_v2.programming_task_adapter import install_offline_runner

from app.core.config import Settings
from app.edu.programming_live import install_live_runner
from app.edu.programming_worker import ProgrammingWorker
from app.main import create_app

from edu.test_c01_database_evidence import upload
from edu.test_programming_task_agent import archive, transport
from edu.test_programming_tasks import URL, bindings

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _approval(assessment: str, snapshot: str, *, campaign: str) -> dict:
    return {
        "campaign_id": campaign,
        "human_approval_reference": f"approval-{campaign}",
        "purpose": "programming_live",
        "model": "qwen3.8-flash",
        "endpoint_sha256": sha256(BASE_URL.encode()).hexdigest(),
        "source_fingerprints": {assessment: snapshot},
        "price_fingerprint": REVIEWED_PRICE_20261119.fingerprint,
        "valid_until": REVIEWED_PRICE_20261119.valid_until,
        "max_requests": 6,
        "max_output_tokens": 800,
        "total_token_limit": 2_000_000,
        "cost_cap_cny": "4.00",
        "independent_review_reference": "independent-review-live02-01",
        "reviewed_code_sha256": code_fingerprint(),
    }


def _settings():
    from app.ai.edu.qwen_config import QwenSettings

    return QwenSettings(SecretStr("test-key"), BASE_URL)


def _production_app(runtime):
    return create_app(
        Settings(environment="production", database_path=runtime.db.path, ai_task_routes=False)
    )


def _write_approval(path, assessment: str, snapshot: str, campaign: str) -> dict:
    approval = _approval(assessment, snapshot, campaign=campaign)
    path.write_text(json.dumps(approval), encoding="utf-8")
    return approval


def test_live_task_runs_through_worker_and_is_readable_after_restart(
    runtime, monkeypatch, tmp_path
):
    approval_path = tmp_path / "programming-approval.json"
    ledger = tmp_path / "ledgers"
    app = _production_app(runtime)
    with TestClient(app) as client:
        monkeypatch.setenv("WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL", str(approval_path))
        monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", str(ledger))
        client.headers["Authorization"] = "Bearer " + runtime.token
        assessment = upload(client, archive(0))
        snapshot = bindings(runtime, assessment)["snapshot"]
        approval = _write_approval(
            approval_path, assessment, snapshot, "live02-synthetic-001"
        )
        service = app.state.programming_tasks
        mock, decisions = transport()
        install_live_runner(service, settings=_settings(), transport=mock)

        response = client.post(
            f"{URL}/{assessment}/ai-tasks", json={"idempotency_key": "live02-real"}
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        assert response.json()["state"] == "queued"
        assert response.json()["execution_origin"] == "none"

        # FastAPI must not run it: the durable worker owns the paid campaign.
        assert ProgrammingWorker(service).run_once() == 1
        view = client.get(f"{URL}/{assessment}/ai-tasks/{task_id}").json()
        assert view["state"] == "completed", view
        assert (view["execution_origin"], view["real_qwen"], view["production_enabled"]) == (
            "qwen_live",
            "completed",
            True,
        )
        assert [decision["name"] for decision in decisions] == [OVERVIEW, INSPECT, "finish"]
        assert (view["model_requests"], view["tool_attempts"]) == (3, 2)
        with runtime.db.transaction() as connection:
            row = connection.execute(
                "SELECT provenance_json,result_sha FROM ai_programming_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            events = connection.execute(
                "SELECT phase,payload FROM ai_programming_events WHERE task_id=?",
                (task_id,),
            ).fetchall()
        provenance = json.loads(row["provenance_json"])
        assert provenance["approval_reference"] == approval["human_approval_reference"]
        assert provenance["budget"]["request_count"] == 3
        assert provenance["budget"]["tool_attempts"] == 2
        assert provenance["real_qwen"] == "completed"
        phases = [event["phase"] for event in events]
        assert {"queued", "running", "model_budget", "tool_audit", "run_summary"} <= set(phases)
        wire = json.dumps([dict(event) for event in events])
        for marker in ("synthetic-private", "PRIVATE_CODE", "prompt", "api_key"):
            assert marker not in wire

    # Restart with no approval configured: the completed row and original bytes
    # remain readable; no result is reconstructed from model memory.
    monkeypatch.delenv("WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL")
    monkeypatch.delenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT")
    restarted = _production_app(runtime)
    with TestClient(restarted) as client:
        client.headers["Authorization"] = "Bearer " + runtime.token
        again = client.get(f"{URL}/{assessment}/ai-tasks/{task_id}")
        assert again.status_code == 200, again.text
        assert again.json() == view


def test_live_without_approval_is_rejected_before_a_task_row(runtime):
    app = _production_app(runtime)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + runtime.token
        assessment = upload(client, archive(0))
        response = client.post(
            f"{URL}/{assessment}/ai-tasks", json={"idempotency_key": "no-live"}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AI_LIVE_ADMISSION_REQUIRED"
        with runtime.db.transaction() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM ai_programming_tasks WHERE teacher_id=?",
                    (runtime.teacher,),
                ).fetchone()[0]
                == 0
            )


def test_live_source_outside_approval_is_rejected(runtime, monkeypatch, tmp_path):
    approval_path = tmp_path / "programming-approval.json"
    ledger = tmp_path / "ledgers"
    app = _production_app(runtime)
    with TestClient(app) as client:
        monkeypatch.setenv("WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL", str(approval_path))
        monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", str(ledger))
        client.headers["Authorization"] = "Bearer " + runtime.token
        assessment = upload(client, archive(0))
        _write_approval(approval_path, assessment, "0" * 64, "live02-source-block")
        service = app.state.programming_tasks
        install_live_runner(service, settings=_settings(), transport=transport()[0])
        response = client.post(
            f"{URL}/{assessment}/ai-tasks", json={"idempotency_key": "wrong-source"}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "AI_LIVE_SOURCE_NOT_APPROVED"


def test_worker_expiry_never_claims_success(runtime, monkeypatch, tmp_path):
    approval_path = tmp_path / "programming-approval.json"
    ledger = tmp_path / "ledgers"
    app = _production_app(runtime)
    with TestClient(app) as client:
        monkeypatch.setenv("WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL", str(approval_path))
        monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", str(ledger))
        client.headers["Authorization"] = "Bearer " + runtime.token
        assessment = upload(client, archive(0))
        snapshot = bindings(runtime, assessment)["snapshot"]
        _write_approval(approval_path, assessment, snapshot, "live02-timeout")
        service = app.state.programming_tasks
        install_live_runner(service, settings=_settings(), transport=transport()[0])
        response = client.post(
            f"{URL}/{assessment}/ai-tasks",
            json={"idempotency_key": "timeout", "timeout_seconds": 1},
        )
        task_id = response.json()["task_id"]
        service.clock = lambda: datetime.now(UTC).timestamp() + 10
        assert ProgrammingWorker(service).run_once() == 0
        view = client.get(f"{URL}/{assessment}/ai-tasks/{task_id}").json()
        assert view["state"] == "timed_out"
        assert view["result"] is None
        assert view["real_qwen"] == "not_run"


def test_offline_runner_is_not_installable_on_production(runtime):
    app = _production_app(runtime)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + runtime.token
        assessment = upload(client, archive(0))
        service = app.state.programming_tasks
        with pytest.raises(ValueError, match="offline_programming_test_host_required"):
            install_offline_runner(service, transport()[0])
        assert assessment
