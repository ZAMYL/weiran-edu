"""B-04D-02：正式任务的可审计工具轨迹（SQLite 持久化，脱敏，可重启回查）。

事件来自真实执行器（durable_audit）与服务端预算记账，不是 result_json 的复述。
"""

import json

import httpx
from prototypes.research_agent_v2.programming import INSPECT, OVERVIEW
from prototypes.research_agent_v2.programming_task_adapter import install_offline_runner

from app.edu.programming_tasks import ProgrammerTaskCreate
from edu.test_c01_database_evidence import PRIVATE_CODE, upload
from edu.test_programming_task_agent import archive, transport
from edu.test_programming_tasks import URL, submit


def _phases(events, name):
    return [event for event in events if event["phase"] == name]


def test_completed_task_persists_tool_audit_fact_links_and_budget(client_factory, runtime):
    with client_factory(runtime.token) as client:
        assessment = upload(client, archive(0))
        mock, decisions = transport()
        install_offline_runner(client.app.state.programming_tasks, mock)
        response, view = submit(client, assessment, "audit-completed")
        assert response.status_code == 202
        assert view["state"] == "completed", view
        task_id = view["task_id"]
        events = client.app.state.programming_tasks.events(task_id)

        audits = _phases(events, "tool_audit")
        assert [(a["payload"]["tool"], a["payload"]["phase"]) for a in audits] == [
            (OVERVIEW, "attempt"),
            (OVERVIEW, "verified"),
            (INSPECT, "attempt"),
            (INSPECT, "verified"),
        ]
        assert [a["payload"]["attempt"] for a in audits] == [1, 1, 2, 2]
        assert [a["payload"]["status"] for a in audits] == ["attempted", "verified"] * 2
        sequences = [a["sequence"] for a in audits]
        assert sequences == sorted(sequences)
        overview_fact = audits[1]["payload"]["fact_ids"][0]
        inspect_fact = audits[3]["payload"]["fact_ids"][0]
        # inspect 的入参只能引用已核验的 overview fact；方向别名不含原始题号。
        assert audits[2]["payload"]["arguments"] == {
            "overview_id": overview_fact,
            "direction": audits[3]["payload"]["arguments"]["direction"],
        }

        (summary,) = _phases(events, "run_summary")
        assert summary["payload"]["status"] == "completed"
        assert summary["payload"]["selected_fact_ids"] == [inspect_fact]
        assert summary["payload"]["finding_count"] == 1
        # 最终采用的 fact_id 与发布结果的 finding_id 一一对应。
        assert [f["finding_id"] for f in view["result"]["findings"]] == [inspect_fact]
        # 预算对账：执行会话、任务行视图与 model_budget 账本事件三方一致。
        assert (
            (summary["payload"]["model_requests"], summary["payload"]["tool_attempts"])
            == (
                view["model_requests"],
                view["tool_attempts"],
            )
            == (3, 2)
        )
        budgets = _phases(events, "model_budget")
        assert budgets[-1]["payload"] == {"request_count": 3, "tool_attempts": 2}
        assert len(decisions) == 3

        # 脱敏：全部事件载荷不得出现源码、姓名、原始路径、题号原文或提示词结构。
        blob = json.dumps(events, ensure_ascii=False)
        for marker in (
            PRIVATE_CODE,
            "61302",
            "restricted-",
            "S001",
            "synthetic-private-name",
            "包/正式答卷",
            "messages",
            "prompt",
        ):
            assert marker not in blob

    # 进程重启后仍可审计：新应用/新服务实例读同一 SQLite，不依赖内存 run.tools。
    with client_factory(runtime.token) as restarted:
        assert restarted.app.state.programming_tasks.events(task_id) == events
        reopened = restarted.get(f"{URL}/{assessment}/ai-tasks/{task_id}")
        assert reopened.status_code == 200 and reopened.json() == view


def test_insufficient_evidence_allows_overview_finish_with_audit(client):
    assessment = upload(client, archive(0, unknown=True))
    mock, _ = transport()
    install_offline_runner(client.app.state.programming_tasks, mock)
    _, view = submit(client, assessment, "audit-insufficient")
    assert view["state"] == "completed" and view["result"]["findings"] == []
    events = client.app.state.programming_tasks.events(view["task_id"])
    audits = _phases(events, "tool_audit")
    assert [(a["payload"]["tool"], a["payload"]["phase"]) for a in audits] == [
        (OVERVIEW, "attempt"),
        (OVERVIEW, "verified"),
    ]
    (summary,) = _phases(events, "run_summary")
    assert summary["payload"]["selected_fact_ids"] == []
    assert summary["payload"]["reason"] == "insufficient_data"
    assert summary["payload"]["finding_count"] == 0
    assert len(_phases(events, "result_committed")) == 1


def test_failed_task_keeps_tool_audit_and_never_commits_result(client, runtime):
    assessment = upload(client, archive(0))
    mock, _ = transport(exhaust=True)
    install_offline_runner(client.app.state.programming_tasks, mock)
    _, view = submit(client, assessment, "audit-exhaust")
    assert view["state"] == "failed" and view["result"] is None
    events = client.app.state.programming_tasks.events(view["task_id"])
    audits = _phases(events, "tool_audit")
    assert audits[0]["payload"]["tool"] == OVERVIEW
    rejected = [a for a in audits if a["payload"]["status"] == "rejected"]
    assert rejected and all(a["payload"]["error"] for a in rejected)
    assert all(a["payload"]["fact_ids"] == [] for a in rejected)
    (summary,) = _phases(events, "run_summary")
    assert summary["payload"]["status"] == "failed"
    assert summary["payload"]["selected_fact_ids"] == []
    assert (
        (summary["payload"]["model_requests"], summary["payload"]["tool_attempts"])
        == (
            view["model_requests"],
            view["tool_attempts"],
        )
        == (6, 5)
    )
    assert not _phases(events, "result_committed")
    stopped = _phases(events, "stopped_without_result")
    assert stopped and stopped[-1]["payload"]["code"] == "AI_TOOL_BUDGET_EXHAUSTED"
    with runtime.db.transaction() as connection:
        row = connection.execute(
            "SELECT result_json FROM ai_programming_tasks WHERE task_id=?", (view["task_id"],)
        ).fetchone()
    assert row["result_json"] is None


def test_cancelled_task_keeps_partial_audit_without_result(client, runtime):
    assessment = upload(client, archive(0))
    service = client.app.state.programming_tasks
    task_id = None
    base, decisions = transport()

    def cancel_after_second_decision(request):
        response = base.handler(request)
        if len(decisions) == 2:
            # overview 已真实执行；inspect 仅由模型提出，取消后不得执行。
            service.cancel(runtime.p, task_id)
        return response

    install_offline_runner(service, httpx.MockTransport(cancel_after_second_decision))
    row, fresh = service.create(
        runtime.p, assessment, ProgrammerTaskCreate(idempotency_key="audit-cancel")
    )
    assert fresh
    task_id = row["task_id"]
    service.execute(task_id)

    assert len(decisions) == 2
    events = service.events(task_id)
    audits = _phases(events, "tool_audit")
    assert [(a["payload"]["tool"], a["payload"]["phase"]) for a in audits] == [
        (OVERVIEW, "attempt"),
        (OVERVIEW, "verified"),
    ]
    stopped = _phases(events, "stopped_without_result")
    assert stopped and all(e["payload"]["code"] == "AI_TASK_CANCELLED" for e in stopped)
    assert not _phases(events, "result_committed")
    with runtime.db.transaction() as connection:
        current = connection.execute(
            "SELECT state,result_json FROM ai_programming_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
    assert current["state"] == "cancelled" and current["result_json"] is None


def test_completed_result_rejects_tampered_original_after_restart(client_factory, runtime):
    with client_factory(runtime.token) as client:
        assessment = upload(client, archive(0))
        mock, _ = transport()
        install_offline_runner(client.app.state.programming_tasks, mock)
        _, view = submit(client, assessment, "audit-tamper")
        assert view["state"] == "completed"
        task_id = view["task_id"]
        sha = client.app.state.assessment_reader.overview(runtime.p, assessment).assets[0].sha256
        client.app.state.programming_tasks.originals.path_for(sha).write_bytes(
            b"tampered after completion"
        )
    with client_factory(runtime.token) as restarted:
        response = restarted.get(f"{URL}/{assessment}/ai-tasks/{task_id}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SOURCE_CHANGED"
