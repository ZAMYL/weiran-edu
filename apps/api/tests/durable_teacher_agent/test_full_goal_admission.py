"""Full-request admission through real synthetic FastAPI/SQLite and the HTTP Mock."""

import json

import pytest
from fastapi.testclient import TestClient
from prototypes.durable_teacher_agent.service import Invocation
from prototypes.research_agent_v2.runtime import ResearchExecutor

from app.ai.edu.qwen_config import QwenSettings

from .test_tasks import actor, setup, upload

P1_REQUESTS = (
    "比较 I01 与 I02 的关联，并列出每位学生的名字",
    "Compare I01 and I02 correlation and list each student's full name",
    "Compare I01 and I02 correlation in another batch",
)


@pytest.mark.parametrize("question", P1_REQUESTS, ids=("p1-zh-name", "p1-en-name", "p1-batch"))
def test_full_question_denied_before_campaign_then_legal_task_runs(tmp_path, monkeypatch, question):
    def no_live_settings():
        raise AssertionError("real_model_settings_must_not_be_read")

    monkeypatch.setattr(QwenSettings, "from_env", no_live_settings)
    fixture, service = setup(tmp_path)
    workers, budgets, tools = [], [], []
    execute = service.execute
    open_budget = Invocation.open_budget
    execute_tool = ResearchExecutor.execute

    def worker(task_id):
        workers.append(task_id)
        return execute(task_id)

    def budget(invocation, permit):
        budgets.append(permit.campaign_id)
        return open_budget(invocation, permit)

    def tool(executor, *args, **kwargs):
        tools.append(args[0])
        return execute_tool(executor, *args, **kwargs)

    monkeypatch.setattr(service, "execute", worker)
    monkeypatch.setattr(Invocation, "open_budget", budget)
    monkeypatch.setattr(ResearchExecutor, "execute", tool)
    try:
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            batch = upload(client, fixture)
            service.store.grant(actor(fixture, service), batch["batch_id"], batch["snapshot_id"])
            base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
            body = {
                "snapshot_id": batch["snapshot_id"],
                "question": question,
                "idempotency_key": "rejected-full-question",
            }
            response = client.post(base, json=body)
            with service.store.database.transaction() as connection:
                counts = {
                    table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in ("ai_synthetic_tasks", "ai_synthetic_events")
                }
            before_legal = {
                "http": response.status_code,
                "error": response.json().get("error", {}).get("code"),
                **counts,
                "worker_calls": len(workers),
                "budget_opens": len(budgets),
                "tool_calls": len(tools),
                "campaign_journals": len(list((tmp_path / "campaigns").rglob("*.jsonl"))),
            }
            legal = client.post(
                base,
                json=body
                | {"question": "有哪些值得关注的问题？", "idempotency_key": "legal-after-denial"},
            )
            print(
                "ADMISSION_EVIDENCE="
                + json.dumps(
                    {
                        "request": question,
                        "before_legal": before_legal,
                        "legal_http": legal.status_code,
                        "legal_error": legal.json().get("error", {}).get("code"),
                    },
                    ensure_ascii=False,
                )
            )
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == "AI_GOAL_UNSUPPORTED"
            assert counts == {"ai_synthetic_tasks": 0, "ai_synthetic_events": 0}
            assert before_legal["worker_calls"] == 0
            assert before_legal["budget_opens"] == before_legal["tool_calls"] == 0
            assert before_legal["campaign_journals"] == 0
            assert legal.status_code == 202, legal.text
            task_id = legal.json()["task_id"]
            result = client.get(base + "/" + task_id, params={"snapshot_id": batch["snapshot_id"]})
            assert result.status_code == 200, result.text
            view = result.json()
            assert view["state"] == "completed", view
            assert workers == [task_id] and len(budgets) == 1 and tools
            assert 0 < view["model_requests"] <= 6 and 0 < view["tool_attempts"] <= 5
            assert view["result"]["real_qwen"] == "not_run"
            assert view["result"]["budget"]["actual_remote_requests"] == 0
            with service.store.database.transaction() as connection:
                assert (
                    connection.execute("SELECT count(*) FROM ai_synthetic_tasks").fetchone()[0] == 1
                )
            assert len(list((tmp_path / "campaigns").rglob("*.jsonl"))) == 1
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)


def test_extra_intents_are_denied_without_consuming_the_next_legal_campaign(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("denied_goal_must_not_start_worker_or_open_budget")

    monkeypatch.setattr(QwenSettings, "from_env", forbidden)
    fixture, service = setup(tmp_path)
    questions = (
        "优先核查 I01、I02，再画一只猫",
        "核查 I01 缺分，再画一只猫",
        "调查 I01 和 I02 相关，再画一只猫",
        "比较 I01 和 I02 的群体差异，再画一只猫",
        "为什么 I01 和 I02 相关，再画一只猫",
        "优先核查 I01 并核查 I02 缺分",
        "比较 I01 与 I02 的关联；核查 I01 缺分",
        "比较 I01 与 I02 的关联，读取隔壁班的数据",
        "Compare I01 and I02 correlation using a different assessment",
        "Compare I01 and I02 correlation and show each participant's legal name",
        "比较 I01 和 I02 的群体差异，展示每个人的个人资料",
        "优先核查 I01 然后展示每个人的全名",
        "为什么 I01 和 I02 相关，然后获取所有人的身份",
        "correlation pineapple I01 I02",
        "relation I01 I02 missing",
        "有哪些值得关注的问题？优先核查 I01",
        "优先核查 I01 I02 arbitrary",
        "优先核查 i01",
        "调查I01000和I02的关联",
        "调查 I01 和 I99 相关",
    )
    try:
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            batch = upload(client, fixture)
            service.store.grant(actor(fixture, service), batch["batch_id"], batch["snapshot_id"])
            base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
            body = {
                "snapshot_id": batch["snapshot_id"],
                "idempotency_key": "unchanged-after-denial",
            }
            with monkeypatch.context() as denial:
                denial.setattr(service, "execute", forbidden)
                denial.setattr(Invocation, "open_budget", forbidden)
                for question in questions:
                    response = client.post(base, json=body | {"question": question})
                    assert response.status_code == 422, (question, response.text)
                    assert response.json()["error"]["code"] == "AI_GOAL_UNSUPPORTED"
                    with service.store.database.transaction() as connection:
                        for table in ("ai_synthetic_tasks", "ai_synthetic_events"):
                            assert (
                                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                                == 0
                            )
                    assert not list((tmp_path / "campaigns").rglob("*"))
            # Reusing the same idempotency key proves even rejected inputs did not claim it.
            response = client.post(base, json=body | {"question": "优先核查 I01、I02"})
            assert response.status_code == 202, response.text
            view = client.get(
                base + "/" + response.json()["task_id"],
                params={"snapshot_id": batch["snapshot_id"]},
            ).json()
            assert view["state"] == "completed", view
            assert view["result"]["real_qwen"] == "not_run"
            assert view["result"]["budget"]["actual_remote_requests"] == 0
            assert 0 < view["model_requests"] <= 6 and 0 < view["tool_attempts"] <= 5
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)
