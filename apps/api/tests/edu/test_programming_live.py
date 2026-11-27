"""LIVE-02：正式任务行上的真实 Qwen 接线、Worker 认领与失败封闭。"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261125
from prototypes.qwen_execution.runtime import code_fingerprint
from prototypes.research_agent_v2.programming import INSPECT, OVERVIEW
from prototypes.research_agent_v2.programming_task_adapter import install_offline_runner
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import APIError
from app.edu.__main__ import main as admin_main
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
        "price_fingerprint": REVIEWED_PRICE_20261125.fingerprint,
        "valid_until": REVIEWED_PRICE_20261125.valid_until,
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
        approval = _write_approval(approval_path, assessment, snapshot, "live02-synthetic-001")
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
            "http_mock",
            "not_run",
            False,
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
        assert provenance["execution_mode"] == "test_mock"
        assert provenance["real_qwen"] == "not_run"
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
        response = client.post(f"{URL}/{assessment}/ai-tasks", json={"idempotency_key": "no-live"})
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


def test_second_claim_of_one_row_can_never_run_twice(runtime, monkeypatch, tmp_path):
    """条件 UPDATE 的语义：同一行只允许一个 worker 认领，第二个必须被拒。"""
    approval_path = tmp_path / "programming-approval.json"
    ledger = tmp_path / "ledgers"
    app = _production_app(runtime)
    with TestClient(app) as client:
        monkeypatch.setenv("WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL", str(approval_path))
        monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", str(ledger))
        client.headers["Authorization"] = "Bearer " + runtime.token
        assessment = upload(client, archive(0))
        snapshot = bindings(runtime, assessment)["snapshot"]
        _write_approval(approval_path, assessment, snapshot, "live02-double-claim")
        service = app.state.programming_tasks
        install_live_runner(service, settings=_settings(), transport=transport()[0])
        response = client.post(
            f"{URL}/{assessment}/ai-tasks", json={"idempotency_key": "double-claim"}
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]

        assert service.start(task_id)["state"] == "running"
        with pytest.raises(APIError) as refused:
            service.start(task_id)
        assert refused.value.code == "AI_TASK_NOT_RUNNABLE"
        with runtime.db.transaction() as connection:
            row = connection.execute(
                "SELECT state,budget_json FROM ai_programming_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        assert row["state"] == "running"
        # 第二个认领者被拒，因此预算仍是一次都没发生。
        assert json.loads(row["budget_json"])["request_count"] == 0


def test_crashed_running_row_is_swept_and_never_reports_success(runtime, monkeypatch, tmp_path):
    """worker 崩溃后重启：过期 running 行被扫成 timed_out，不复活、不冒充成功。"""
    approval_path = tmp_path / "programming-approval.json"
    ledger = tmp_path / "ledgers"
    app = _production_app(runtime)
    with TestClient(app) as client:
        monkeypatch.setenv("WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL", str(approval_path))
        monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", str(ledger))
        client.headers["Authorization"] = "Bearer " + runtime.token
        assessment = upload(client, archive(0))
        snapshot = bindings(runtime, assessment)["snapshot"]
        _write_approval(approval_path, assessment, snapshot, "live02-crash")
        service = app.state.programming_tasks
        install_live_runner(service, settings=_settings(), transport=transport()[0])
        response = client.post(
            f"{URL}/{assessment}/ai-tasks",
            json={"idempotency_key": "crash", "timeout_seconds": 1},
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        assert service.start(task_id)["state"] == "running"

        # 模拟进程在 running 状态被杀：没有 finish，也没有 fail，只剩一行 running。
        service.clock = lambda: datetime.now(UTC).timestamp() + 10
        assert service.queued() == []
        assert ProgrammingWorker(service).run_once() == 0

        with runtime.db.transaction() as connection:
            row = connection.execute(
                "SELECT state,error_code,result_json,result_sha FROM ai_programming_tasks "
                "WHERE task_id=?",
                (task_id,),
            ).fetchone()
        assert row["state"] == "timed_out"
        assert row["error_code"] == "AI_TASK_TIMEOUT"
        assert row["result_json"] is None and row["result_sha"] is None

        view = client.get(f"{URL}/{assessment}/ai-tasks/{task_id}").json()
        assert view["state"] == "timed_out"
        assert view["result"] is None
        assert view["real_qwen"] in ("not_run", "failed")
        assert view["production_enabled"] is False


def test_stale_approval_keeps_api_up_and_programming_fails_closed(runtime, monkeypatch, tmp_path):
    """审批过期只关编程任务，不拖垮上传/查询：启动不抛异常，任务 409。"""
    approval_path = tmp_path / "programming-approval.json"
    ledger = tmp_path / "ledgers"
    approval = _approval("0" * 64, "0" * 64, campaign="live02-stale")
    approval["reviewed_code_sha256"] = "0" * 64
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    monkeypatch.setenv("WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL", str(approval_path))
    monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", str(ledger))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", BASE_URL)
    monkeypatch.setenv("WEIRAN_QWEN_MODEL", "qwen3.8-flash")

    app = _production_app(runtime)  # 启动不再失败关闭整站
    assert str(app.state.programming_live).startswith("unavailable"), app.state.programming_live

    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + runtime.token
        assessment = upload(client, archive(0))
        response = client.post(
            f"{URL}/{assessment}/ai-tasks", json={"idempotency_key": "stale-approval"}
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "AI_LIVE_ADMISSION_REQUIRED"
        # 上传与查询仍然可用，说明只有编程调查被拒。
        assert client.get(f"{URL}/{assessment}").status_code == 200


def test_operator_cli_revokes_programming_grant(runtime, monkeypatch, tmp_path):
    """撤权走 operator CLI：改库即撤销，任务转 revoked 且结果清空。"""
    approval_path = tmp_path / "programming-approval.json"
    ledger = tmp_path / "ledgers"
    app = _production_app(runtime)
    with TestClient(app) as client:
        monkeypatch.setenv("WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL", str(approval_path))
        monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", str(ledger))
        client.headers["Authorization"] = "Bearer " + runtime.token
        assessment = upload(client, archive(0))
        snapshot = bindings(runtime, assessment)["snapshot"]
        _write_approval(approval_path, assessment, snapshot, "live02-cli-revoke")
        service = app.state.programming_tasks
        install_live_runner(service, settings=_settings(), transport=transport()[0])
        response = client.post(
            f"{URL}/{assessment}/ai-tasks", json={"idempotency_key": "cli-revoke"}
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "edu",
                "--database",
                str(runtime.db.path),
                "programming-revoke",
                "--assessment-id",
                assessment,
            ],
        )
        assert admin_main() == 0

        revoked = client.get(f"{URL}/{assessment}/ai-tasks/{task_id}")
        assert revoked.status_code == 403
        assert revoked.json()["error"]["code"] == "AI_PURPOSE_REVOKED"
        with runtime.db.transaction() as connection:
            row = connection.execute(
                "SELECT state,result_json,result_sha FROM ai_programming_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            grant = connection.execute(
                "SELECT revoked FROM ai_programming_grants WHERE assessment_id=?",
                (assessment,),
            ).fetchone()
        assert row["state"] == "revoked"
        assert row["result_json"] is None and row["result_sha"] is None
        assert grant["revoked"] == 1
