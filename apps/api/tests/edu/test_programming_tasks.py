"""B-04C：编程调查任务的正式入口、可信持久化与反例。

执行器为测试注入的受控实现（`http_mock`），不触网、不记假账；
任何结果都必须落在本测评已入库的证据上，否则整条拒绝。
"""

import json

import pytest

from app.core.errors import APIError
from app.edu.programming_tasks import (
    PURPOSE_LIVE,
    PURPOSE_MOCK,
    ProgrammerTaskCreate,
    ProgrammingResult,
)
from edu.answersheet_samples import paper_html, zip_of

URL = "/api/v1/edu/assessments"


def payload(
    *, key="S001", name="周同学", code="int main(){ return 1; }", verdict="答案正确"
) -> bytes:
    return zip_of(
        {
            "包/正式答卷/答卷.html": paper_html(
                key=key,
                name=name,
                code=code,
                rows=[
                    ("1", verdict, "5" if verdict == "答案正确" else "0", "1.00 ms", "300 KB"),
                    ("2", "答案错误", "0", "9.00 ms", "900 KB"),
                ],
            )
        }
    )


def upload(client, body: bytes | None = None) -> dict:
    response = client.post(URL, files=[("file", ("甲班.zip", body or payload()))])
    assert response.status_code == 201, response.text
    return response.json()


def submit(client, assessment_id: str, key: str, **extra) -> tuple[object, dict]:
    """提交后按契约回读状态：202 只表示已受理，终态必须查库。"""
    response = client.post(
        f"{URL}/{assessment_id}/ai-tasks", json={"idempotency_key": key, **extra}
    )
    if response.status_code not in (200, 202):
        return response, response.json()
    task_id = response.json()["task_id"]
    return response, client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}").json()


def bindings(runtime, assessment_id: str) -> dict:
    """服务端视角的证据锚点：资产、学生、题目与题块位置都取自入库记录。"""
    with runtime.db.transaction() as connection:
        asset = connection.execute(
            "SELECT asset_id,sha256 FROM assessment_assets WHERE assessment_id=?",
            (assessment_id,),
        ).fetchone()
        student = connection.execute(
            "SELECT student_pk,external_key FROM assessment_participants WHERE assessment_id=?",
            (assessment_id,),
        ).fetchone()
        problem = connection.execute(
            "SELECT label FROM problems WHERE assessment_id=?", (assessment_id,)
        ).fetchone()
        submission = connection.execute(
            "SELECT source_index FROM submissions WHERE assessment_id=?", (assessment_id,)
        ).fetchone()
        assessment = connection.execute(
            "SELECT source_snapshot_id FROM assessments WHERE assessment_id=?", (assessment_id,)
        ).fetchone()
    return {
        "snapshot": assessment["source_snapshot_id"],
        "asset_id": asset["asset_id"],
        "sha256": asset["sha256"],
        "student": student["external_key"],
        "label": problem["label"],
        "source_index": submission["source_index"],
    }


def _unused(service, task_id, row, actor, guard):
    raise AssertionError("本用例不应真的执行受控执行器")


def make_runner(assessment_id: str, snapshot: str, bound: dict, *, code="AI_EVIDENCE_NOT_FOUND"):
    def runner(service, task_id, row, actor, guard):
        guard()
        service.budget(task_id, request_count=1, tool_attempts=1)
        guard()
        return ProgrammingResult(
            assessment_id=assessment_id,
            source_snapshot_id=snapshot,
            asset_sha256=[bound["sha256"]],
            statement="该题在第 2 个测试点失败",
            findings=[
                {
                    "finding_id": "F1",
                    "code": code,
                    "statement": "边界输入判错",
                    "evidence": [
                        {
                            "asset_id": bound["asset_id"],
                            "source_entry": "包/正式答卷/答卷.html",
                            "student_key": bound["student"],
                            "problem_label": bound["label"],
                            "source_index": bound["source_index"],
                            "test_index": 2,
                        },
                    ],
                },
            ],
        )

    return runner


@pytest.fixture
def assessment(client, runtime):
    info = upload(client)
    return dict(info, snapshot=bindings(runtime, info["assessment_id"])["snapshot"])


def test_valid_task_creates_persists_and_survives_restart(
    client, runtime, assessment, client_factory
):
    service = client.app.state.programming_tasks
    bound = bindings(runtime, assessment["assessment_id"])
    service.runner = make_runner(assessment["assessment_id"], assessment["snapshot"], bound)

    response, created = submit(client, assessment["assessment_id"], "k-1")
    assert response.status_code == 202, response.text
    assert created["state"] == "completed"
    assert created["execution_origin"] == "http_mock"
    assert created["real_qwen"] == "not_run"
    assert created["production_enabled"] is False
    assert created["source_snapshot_id"] == assessment["snapshot"]

    body = client.get(f"{URL}/{assessment['assessment_id']}/ai-tasks/{created['task_id']}").json()
    assert body["state"] == "completed"
    assert body["result"]["findings"][0]["evidence"][0]["test_index"] == 2

    with runtime.db.transaction() as connection:
        row = connection.execute(
            "SELECT state,result_sha,result_json,campaign_id,teacher_id,credential_sha "
            "FROM ai_programming_tasks WHERE task_id=?",
            (created["task_id"],),
        ).fetchone()
    assert row["campaign_id"].startswith("assessment-")
    assert row["state"] == "completed"
    assert row["teacher_id"] == runtime.teacher
    assert row["credential_sha"] == runtime.p.token_hash
    assert row["result_sha"] is not None and row["result_json"]

    with client_factory(runtime.token) as restarted:
        again = restarted.get(f"{URL}/{assessment['assessment_id']}/ai-tasks/{created['task_id']}")
        assert again.status_code == 200
        assert again.json()["result"]["statement"] == "该题在第 2 个测试点失败"
        assert again.json()["state"] == "completed"


def test_task_is_bound_to_teacher_and_assessment(client, runtime, assessment):
    service = client.app.state.programming_tasks
    bound = bindings(runtime, assessment["assessment_id"])
    service.runner = make_runner(assessment["assessment_id"], assessment["snapshot"], bound)
    token = runtime.other_token
    _, created = submit(client, assessment["assessment_id"], "k-own")

    denied = client.post(
        f"{URL}/{assessment['assessment_id']}/ai-tasks",
        json={"idempotency_key": "k-other"},
        headers={"Authorization": "Bearer " + token},
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    hidden = client.get(
        f"{URL}/{assessment['assessment_id']}/ai-tasks/{created['task_id']}",
        headers={"Authorization": "Bearer " + token},
    )
    assert hidden.status_code == 404


def test_source_change_and_revocation_block_result(client, runtime, assessment):
    service = client.app.state.programming_tasks
    bound = bindings(runtime, assessment["assessment_id"])
    service.runner = make_runner(assessment["assessment_id"], assessment["snapshot"], bound)
    _, created = submit(client, assessment["assessment_id"], "k-src")

    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE assessment_assets SET sha256=? WHERE assessment_id=?",
            ("0" * 64, assessment["assessment_id"]),
        )
    changed = client.get(f"{URL}/{assessment['assessment_id']}/ai-tasks/{created['task_id']}")
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "AI_SOURCE_CONFLICT"


def test_revocation_erases_unpublished_result(client, runtime, assessment):
    service = client.app.state.programming_tasks
    service.runner = _unused
    created = service.create(
        runtime.p,
        assessment["assessment_id"],
        ProgrammerTaskCreate(idempotency_key="k-revoke"),
    )[0]
    assert (
        client.get(f"{URL}/{assessment['assessment_id']}/ai-tasks/{created['task_id']}").json()[
            "state"
        ]
        == "queued"
    )
    service.revoke(assessment["assessment_id"])
    revoked = client.get(f"{URL}/{assessment['assessment_id']}/ai-tasks/{created['task_id']}")
    assert revoked.status_code == 403
    assert revoked.json()["error"]["code"] == "AI_PURPOSE_REVOKED"
    with runtime.db.transaction() as connection:
        row = connection.execute(
            "SELECT state,result_json,result_sha FROM ai_programming_tasks WHERE task_id=?",
            (created["task_id"],),
        ).fetchone()
    assert row["state"] == "revoked"
    assert row["result_json"] is None and row["result_sha"] is None


def test_missing_admission_is_a_stable_refusal(runtime, client_factory, client, assessment):
    with client_factory(runtime.token) as fresh:
        fresh.app.state.programming_tasks.runner = None
        refused = fresh.post(
            f"{URL}/{assessment['assessment_id']}/ai-tasks",
            json={"idempotency_key": "k-live"},
        )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "AI_LIVE_ADMISSION_REQUIRED"
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_programming_tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ai_programming_grants").fetchone()[0] == 0


def test_duplicate_submission_contract(client, runtime, assessment):
    service = client.app.state.programming_tasks
    service.runner = _unused
    first, _ = submit(client, assessment["assessment_id"], "k-dup")
    assert first.status_code == 202
    task_id = first.json()["task_id"]

    replay = client.post(
        f"{URL}/{assessment['assessment_id']}/ai-tasks",
        json={"idempotency_key": "k-dup", "timeout_seconds": 120},
    )
    assert replay.status_code == 200
    assert replay.json()["task_id"] == task_id

    conflict = client.post(
        f"{URL}/{assessment['assessment_id']}/ai-tasks",
        json={"idempotency_key": "k-dup", "timeout_seconds": 30},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "AI_IDEMPOTENCY_CONFLICT"

    second = client.post(
        f"{URL}/{assessment['assessment_id']}/ai-tasks", json={"idempotency_key": "k-dup-2"}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "AI_CAMPAIGN_ALREADY_USED"

    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_programming_tasks").fetchone()[0] == 1


def test_cancel_and_budget_reuse_are_rejected(client, runtime, assessment):
    service = client.app.state.programming_tasks
    service.runner = _unused
    created = service.create(
        runtime.p,
        assessment["assessment_id"],
        ProgrammerTaskCreate(idempotency_key="k-cancel"),
    )[0]
    cancelled = client.post(
        f"{URL}/{assessment['assessment_id']}/ai-tasks/{created['task_id']}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    assert cancelled.json()["result"] is None

    service.execute(created["task_id"])
    with runtime.db.transaction() as connection:
        row = connection.execute(
            "SELECT state,result_json FROM ai_programming_tasks WHERE task_id=?",
            (created["task_id"],),
        ).fetchone()
    assert row["state"] == "cancelled" and row["result_json"] is None


def test_budget_is_monotonic_and_capped(runtime, client, assessment):
    service = client.app.state.programming_tasks
    service.runner = _unused
    created = service.create(
        runtime.p,
        assessment["assessment_id"],
        ProgrammerTaskCreate(idempotency_key="k-budget"),
    )[0]
    service.start(created["task_id"])
    service.budget(created["task_id"], request_count=2, tool_attempts=1)
    with pytest.raises(APIError) as reset:
        service.budget(created["task_id"], request_count=1, tool_attempts=1)
    assert reset.value.code == "AI_BUDGET_RESET_FORBIDDEN"
    with pytest.raises(APIError) as capped:
        service.budget(created["task_id"], request_count=7, tool_attempts=1)
    assert capped.value.code == "AI_BUDGET_EXHAUSTED"
    with pytest.raises(APIError) as tools:
        service.budget(created["task_id"], request_count=2, tool_attempts=6)
    assert tools.value.code == "AI_TOOL_BUDGET_EXHAUSTED"


def test_fabricated_evidence_fails_without_result(client, runtime, assessment):
    service = client.app.state.programming_tasks
    bound = bindings(runtime, assessment["assessment_id"])
    runner = make_runner(assessment["assessment_id"], assessment["snapshot"], bound)
    fabricated = dict(bound, student="S999")

    def bad(service_, task_id, row, actor, guard):
        guard()
        service_.budget(task_id, request_count=1, tool_attempts=1)
        guard()
        return ProgrammingResult(
            assessment_id=assessment["assessment_id"],
            source_snapshot_id=assessment["snapshot"],
            asset_sha256=[fabricated["sha256"]],
            statement="伪造",
            findings=[
                {
                    "finding_id": "F1",
                    "code": "X",
                    "statement": "伪造证据",
                    "evidence": [
                        {
                            "asset_id": fabricated["asset_id"],
                            "source_entry": "包/正式答卷/答卷.html",
                            "student_key": fabricated["student"],
                            "problem_label": fabricated["label"],
                            "test_index": 1,
                        },
                    ],
                },
            ],
        )

    assert runner  # 正常结论的形状来自同一批锚点
    service.runner = bad
    _, created = submit(client, assessment["assessment_id"], "k-fake")
    assert created["state"] == "failed"
    assert created["error_code"] == "AI_EVIDENCE_NOT_FOUND"
    assert created["result"] is None


def test_missing_original_blocks_publication(client, runtime, assessment):
    service = client.app.state.programming_tasks
    bound = bindings(runtime, assessment["assessment_id"])
    service.runner = make_runner(assessment["assessment_id"], assessment["snapshot"], bound)
    for path in (runtime.db.path.parent / "originals").rglob("*.zip"):
        path.unlink()
    _, created = submit(client, assessment["assessment_id"], "k-org")
    assert created["state"] == "failed"
    assert created["error_code"] == "ORIGINAL_UNAVAILABLE"
    assert created["result"] is None


def test_assessment_list_is_owner_scoped_and_code_free(client, runtime):
    upload(client)
    listed = client.get(URL, params={"limit": 2, "offset": 0})
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1 and body["limit"] == 2 and body["offset"] == 0
    item = body["items"][0]
    assert set(item) == {
        "assessment_id",
        "title",
        "course",
        "assessment_date",
        "created_at",
        "student_count",
        "problem_count",
        "submission_count",
        "source_snapshot_id",
        "review_state",
        "storage_state",
    }
    assert item["storage_state"] == "stored"
    assert item["source_snapshot_id"]
    assert "code_text" not in json.dumps(body)

    empty = client.get(
        URL,
        params={"limit": 10, "offset": 0},
        headers={"Authorization": "Bearer " + runtime.other_token},
    )
    assert empty.status_code == 200 and empty.json()["items"] == []

    assert client.get(URL, params={"limit": 101}).status_code == 422


def test_mock_origin_is_never_reported_as_qwen(client, runtime, assessment):
    service = client.app.state.programming_tasks
    bound = bindings(runtime, assessment["assessment_id"])
    service.runner = make_runner(assessment["assessment_id"], assessment["snapshot"], bound)
    _, body = submit(client, assessment["assessment_id"], "k-origin")
    assert body["execution_origin"] == "http_mock"
    assert body["real_qwen"] != "completed"
    assert PURPOSE_MOCK == "assessment_mock" and PURPOSE_LIVE == "assessment_live"
