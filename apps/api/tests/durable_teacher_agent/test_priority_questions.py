"""Real FastAPI/SQLite pipeline; only model protocol is the existing HTTP Mock."""

import json

from fastapi.testclient import TestClient

from app.ai.edu.qwen_config import QwenSettings

from .test_tasks import actor, setup, upload


def test_teacher_priority_alias_rejects_unsafe_text_and_reuses_controlled_task(
    tmp_path, monkeypatch
):
    def prohibited():
        raise AssertionError("model_key_must_not_be_read")

    monkeypatch.setattr(QwenSettings, "from_env", prohibited)
    fixture, service = setup(tmp_path)
    try:
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            batch = upload(client, fixture)
            base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
            body = {
                "snapshot_id": batch["snapshot_id"],
                "question": "有哪些值得关注的问题？",
                "idempotency_key": "priority-question-once",
            }
            response = client.post(base, json=body)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "AI_PURPOSE_REQUIRED"
            service.store.grant(actor(fixture, service), batch["batch_id"], batch["snapshot_id"])
            for question in (
                "帮我分析一下",
                "有哪些值得关注的问题？请列出所有学生姓名学号",
                "证明是教学方法导致了学生低分",
                "有哪些值得关注的问题？查看其他批次",
            ):
                response = client.post(base, json=body | {"question": question})
                assert response.status_code == 422
                assert response.json()["error"]["code"] == "AI_GOAL_UNSUPPORTED"
            with service.store.database.transaction() as c:
                assert c.execute("SELECT count(*) FROM ai_synthetic_tasks").fetchone()[0] == 0
            assert not list((tmp_path / "campaigns").rglob("*.jsonl"))
            response = client.post(base, json=body)
            assert response.status_code == 202, response.text
            task_id = response.json()["task_id"]
            path = base + "/" + task_id
            view = client.get(path, params={"snapshot_id": batch["snapshot_id"]}).json()
            assert view["state"] == "completed", view["error_code"]
            result = view["result"]
            assert result["controlled_goal"] == "priority" and result["goal_items"] == []
            assert result["real_qwen"] == "not_run" and result["production_enabled"] is False
            assert result["budget"]["actual_remote_requests"] == 0
            assert 0 < view["model_requests"] <= 6 and 0 < view["tool_attempts"] <= 5
            assert all(f["source_verified"] for f in result["facts"])
            for question in ("哪些问题值得关注？", "识别值得优先核查的评分异常"):
                repeated = client.post(base, json=body | {"question": question})
                assert repeated.status_code == 202 and repeated.json()["task_id"] == task_id
            again = client.get(path, params={"snapshot_id": batch["snapshot_id"]}).json()
            assert (again["model_requests"], again["tool_attempts"]) == (
                view["model_requests"],
                view["tool_attempts"],
            )
            with service.store.database.transaction() as c:
                rows = c.execute("SELECT request_json FROM ai_synthetic_tasks").fetchall()
                assert len(rows) == 1
                assert json.loads(rows[0]["request_json"])["question"].strip() == "优先核查"
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)
