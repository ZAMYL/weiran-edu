"""B-04C-FIX-01：执行期凭证、教师状态与数据范围权限重验。

P0：任务创建后撤销凭证，后台仍能 start/guard/finish 并保存 completed。
本文件定向复现并锁定修复：执行期一律用创建时绑定的 credential_sha 重验，
且拒绝认领后必须落终态，不留永久 queued。
"""

import pytest

from app.core.errors import APIError
from app.edu.programming_tasks import ProgrammerTaskCreate, ProgrammingResult
from edu.answersheet_samples import paper_html, zip_of

URL = "/api/v1/edu/assessments"
ENTRY = "包/正式答卷/答卷.html"


def answer_zip() -> bytes:
    return zip_of(
        {
            ENTRY: paper_html(
                key="S001",
                name="周同学",
                code="int main(){ return 1; }",
                rows=[
                    ("1", "答案正确", "5", "1.00 ms", "300 KB"),
                    ("2", "答案错误", "0", "9.00 ms", "900 KB"),
                ],
            )
        }
    )


def upload(client) -> str:
    response = client.post(URL, files=[("file", ("甲班.zip", answer_zip()))])
    assert response.status_code == 201, response.text
    return response.json()["assessment_id"]


def anchors(runtime, assessment_id: str) -> dict:
    with runtime.db.transaction() as connection:
        asset = connection.execute(
            "SELECT asset_id,sha256 FROM assessment_assets WHERE assessment_id=?",
            (assessment_id,),
        ).fetchone()
        student = connection.execute(
            "SELECT external_key FROM assessment_participants WHERE assessment_id=?",
            (assessment_id,),
        ).fetchone()
        problem = connection.execute(
            "SELECT label FROM problems WHERE assessment_id=?", (assessment_id,)
        ).fetchone()
        snapshot = connection.execute(
            "SELECT source_snapshot_id FROM assessments WHERE assessment_id=?",
            (assessment_id,),
        ).fetchone()[0]
        index = connection.execute(
            "SELECT source_index FROM submissions WHERE assessment_id=?", (assessment_id,)
        ).fetchone()[0]
    return {
        "asset_id": asset["asset_id"],
        "sha256": asset["sha256"],
        "student": student["external_key"],
        "label": problem["label"],
        "snapshot": snapshot,
        "source_index": index,
    }


def valid_result(assessment_id: str, bound: dict) -> ProgrammingResult:
    return ProgrammingResult(
        assessment_id=assessment_id,
        source_snapshot_id=bound["snapshot"],
        asset_sha256=[bound["sha256"]],
        statement="第 2 个测试点失败",
        findings=[
            {
                "finding_id": "F1",
                "code": "BOUNDARY",
                "statement": "边界输入判错",
                "evidence": [
                    {
                        "asset_id": bound["asset_id"],
                        "source_entry": ENTRY,
                        "student_key": bound["student"],
                        "problem_label": bound["label"],
                        "source_index": bound["source_index"],
                        "test_index": 2,
                    }
                ],
            }
        ],
    )


def row_state(runtime, task_id: str) -> dict:
    with runtime.db.transaction() as connection:
        row = connection.execute(
            "SELECT state,error_code,result_json,result_sha,cancelled "
            "FROM ai_programming_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] in ("failed", "cancelled", "revoked", "timed_out", "completed")
    return dict(row)


def unused(*args, **kwargs):
    raise AssertionError("本用例尚未设置受控执行器")


def make_task(client, runtime, assessment_id: str, key: str) -> str:
    service = client.app.state.programming_tasks
    if service.runner is None:
        service.runner = unused
    row, _ = service.create(
        runtime.p, assessment_id, ProgrammerTaskCreate(idempotency_key=key)
    )
    return row["task_id"]


def test_revoked_credentials_cannot_claim_and_leave_terminal_state(
    client, runtime
):
    assessment_id = upload(client)
    service = client.app.state.programming_tasks
    task_id = make_task(client, runtime, assessment_id, "fix-claim")
    runtime.admin.revoke_tokens(runtime.teacher)

    service.runner = lambda service_, task_id_, row, actor, guard: valid_result(
        assessment_id, anchors(runtime, assessment_id)
    )
    service.execute(task_id)

    assert row_state(runtime, task_id) == {
        "state": "failed",
        "error_code": "UNAUTHORIZED",
        "result_json": None,
        "result_sha": None,
        "cancelled": 0,
    }
    assert client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}").status_code == 401


def test_revocation_mid_run_blocks_guard(client, runtime):
    assessment_id = upload(client)
    bound = anchors(runtime, assessment_id)
    service = client.app.state.programming_tasks
    task_id = make_task(client, runtime, assessment_id, "fix-guard")

    def runner(service_, task_id_, row, actor, guard):
        guard()
        service_.budget(task_id_, request_count=1, tool_attempts=1)
        runtime.admin.revoke_tokens(runtime.teacher)
        guard()
        return valid_result(assessment_id, bound)

    service.runner = runner
    service.execute(task_id)
    state = row_state(runtime, task_id)
    assert state["state"] == "failed"
    assert state["error_code"] == "UNAUTHORIZED"
    assert state["result_json"] is None


def test_deactivated_teacher_blocks_finish(client, runtime):
    assessment_id = upload(client)
    bound = anchors(runtime, assessment_id)
    service = client.app.state.programming_tasks
    task_id = make_task(client, runtime, assessment_id, "fix-finish")

    def runner(service_, task_id_, row, actor, guard):
        guard()
        service_.budget(task_id_, request_count=1, tool_attempts=1)
        runtime.admin.set_active(runtime.teacher, False)
        return valid_result(assessment_id, bound)

    service.runner = runner
    service.execute(task_id)
    state = row_state(runtime, task_id)
    assert state["state"] == "failed"
    assert state["error_code"] == "UNAUTHORIZED"
    assert state["result_json"] is None and state["result_sha"] is None


def test_revoked_data_scope_blocks_evidence_access(client, runtime):
    assessment_id = upload(client)
    bound = anchors(runtime, assessment_id)
    service = client.app.state.programming_tasks
    task_id = make_task(client, runtime, assessment_id, "fix-scope")

    def runner(service_, task_id_, row, actor, guard):
        guard()
        service_.budget(task_id_, request_count=1, tool_attempts=1)
        runtime.admin.remove_member(runtime.teacher, runtime.scope)
        guard()
        return valid_result(assessment_id, bound)

    service.runner = runner
    service.execute(task_id)
    state = row_state(runtime, task_id)
    assert state["state"] == "failed"
    assert state["error_code"] == "AI_SCOPE_REVOKED"
    assert state["result_json"] is None

    with pytest.raises(APIError) as denied:
        with runtime.db.transaction() as connection:
            service.check(connection, service._row(connection, task_id), runtime.p, running=True)
    assert denied.value.code == "AI_SCOPE_REVOKED"


def test_valid_credentials_still_complete_with_mock_markers(client, runtime):
    assessment_id = upload(client)
    bound = anchors(runtime, assessment_id)
    service = client.app.state.programming_tasks

    def runner(service_, task_id_, row, actor, guard):
        guard()
        service_.budget(task_id_, request_count=1, tool_attempts=1)
        guard()
        return valid_result(assessment_id, bound)

    service.runner = runner
    task_id = make_task(client, runtime, assessment_id, "fix-ok")
    service.execute(task_id)
    view = client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}").json()
    assert view["state"] == "completed"
    assert view["result"]["statement"] == "第 2 个测试点失败"
    assert view["execution_origin"] == "http_mock"
    assert view["real_qwen"] == "not_run"
    assert view["production_enabled"] is False


def test_revoked_credentials_cannot_read_already_completed_result(client, runtime):
    assessment_id = upload(client)
    bound = anchors(runtime, assessment_id)
    service = client.app.state.programming_tasks

    def runner(service_, task_id_, row, actor, guard):
        guard()
        service_.budget(task_id_, request_count=1, tool_attempts=1)
        guard()
        return valid_result(assessment_id, bound)

    service.runner = runner
    task_id = make_task(client, runtime, assessment_id, "fix-read")
    service.execute(task_id)
    runtime.admin.revoke_tokens(runtime.teacher)
    with pytest.raises(APIError) as denied:
        service.view(runtime.p, task_id)
    assert denied.value.code == "UNAUTHORIZED"
