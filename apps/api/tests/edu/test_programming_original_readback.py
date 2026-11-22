"""B-04C-FIX-02：已完成调查结果的原件回读一致性。

结果发布后，`view()` 必须重新逐字节校验 OriginalStore；
原件缺失、被篡改、多包只坏一包都拒绝返回历史结论，而数据库审计记录保留。
"""

from app.edu.programming_tasks import ProgrammerTaskCreate, ProgrammingResult
from edu.answersheet_samples import paper_html, zip_of

URL = "/api/v1/edu/assessments"
ENTRY = "包/正式答卷/答卷.html"


def package(key: str, name: str, verdict: str = "答案正确") -> bytes:
    return zip_of(
        {
            ENTRY: paper_html(
                key=key,
                name=name,
                code="int main(){ return 0; }",
                rows=[
                    ("1", verdict, "5" if verdict == "答案正确" else "0", "1.00 ms", "300 KB"),
                    ("2", "答案错误", "0", "9.00 ms", "900 KB"),
                ],
            )
        }
    )


def upload(client, packages: list[tuple[str, bytes]] | None = None) -> str:
    files = packages or [("甲班.zip", package("S001", "周同学"))]
    response = client.post(URL, files=[("file", name) for name in files])
    assert response.status_code == 201, response.text
    return response.json()["assessment_id"]


def anchors(runtime, assessment_id: str) -> dict:
    with runtime.db.transaction() as connection:
        assets = connection.execute(
            "SELECT asset_id,sha256,entry FROM assessment_assets WHERE assessment_id=? "
            "ORDER BY source_index",
            (assessment_id,),
        ).fetchall()
        student = connection.execute(
            "SELECT student_pk,external_key,source_asset_id FROM assessment_participants "
            "WHERE assessment_id=? ORDER BY external_key",
            (assessment_id,),
        ).fetchall()
        problem = connection.execute(
            "SELECT label FROM problems WHERE assessment_id=?", (assessment_id,)
        ).fetchone()
        snapshot = connection.execute(
            "SELECT source_snapshot_id FROM assessments WHERE assessment_id=?",
            (assessment_id,),
        ).fetchone()[0]
    first = student[0]
    return {
        "assets": [dict(row) for row in assets],
        "sha256": assets[0]["sha256"],
        "asset_id": first["source_asset_id"],
        "student": first["external_key"],
        "label": problem["label"],
        "snapshot": snapshot,
    }


def investigation(assessment_id: str, bound: dict) -> ProgrammingResult:
    return ProgrammingResult(
        assessment_id=assessment_id,
        source_snapshot_id=bound["snapshot"],
        asset_sha256=[row["sha256"] for row in bound["assets"]],
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
                        "test_index": 2,
                    }
                ],
            }
        ],
    )


def complete(client, runtime, assessment_id: str, key: str) -> str:
    bound = anchors(runtime, assessment_id)
    service = client.app.state.programming_tasks

    def runner(service_, task_id, row, actor, guard):
        guard()
        service_.budget(task_id, request_count=1, tool_attempts=1)
        guard()
        return investigation(assessment_id, bound)

    service.runner = runner
    row, _ = service.create(runtime.p, assessment_id, ProgrammerTaskCreate(idempotency_key=key))
    service.execute(row["task_id"])
    view = client.get(f"{URL}/{assessment_id}/ai-tasks/{row['task_id']}").json()
    assert view["state"] == "completed", view
    return row["task_id"]


def original_path(client, sha256: str):
    return client.app.state.programming_tasks.originals.path_for(sha256)


def stored_row(runtime, task_id: str) -> dict:
    with runtime.db.transaction() as connection:
        row = connection.execute(
            "SELECT state,result_json,result_sha,error_code FROM ai_programming_tasks "
            "WHERE task_id=?",
            (task_id,),
        ).fetchone()
    return dict(row)


def test_tampered_original_is_refused_but_audit_row_is_kept(client, runtime):
    assessment_id = upload(client)
    bound = anchors(runtime, assessment_id)
    task_id = complete(client, runtime, assessment_id, "rb-tamper")
    before = stored_row(runtime, task_id)

    target = original_path(client, bound["sha256"])
    target.write_bytes(target.read_bytes() + b"tampered")

    response = client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_CHANGED"
    assert stored_row(runtime, task_id) == before
    assert before["state"] == "completed" and before["result_sha"]


def test_missing_original_is_refused(client, runtime):
    assessment_id = upload(client)
    bound = anchors(runtime, assessment_id)
    task_id = complete(client, runtime, assessment_id, "rb-missing")
    original_path(client, bound["sha256"]).unlink()

    response = client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORIGINAL_UNAVAILABLE"
    assert stored_row(runtime, task_id)["state"] == "completed"


def test_unchanged_originals_are_readable_after_restart(client, runtime, client_factory):
    assessment_id = upload(client)
    task_id = complete(client, runtime, assessment_id, "rb-restart")
    first = client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}").json()

    with client_factory(runtime.token) as restarted:
        again = restarted.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}")
        assert again.status_code == 200
        assert again.json()["result"]["statement"] == "第 2 个测试点失败"
        assert again.json()["result"]["findings"][0]["code"] == "BOUNDARY"
        assert again.json()["result"] == first["result"]
        assert again.json()["execution_origin"] == "http_mock"
        assert again.json()["real_qwen"] == "not_run"


def test_tampered_result_payload_is_refused(client, runtime):
    assessment_id = upload(client)
    task_id = complete(client, runtime, assessment_id, "rb-result")
    with runtime.db.transaction(write=True) as connection:
        record = connection.execute(
            "SELECT result_json FROM ai_programming_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        forged = record["result_json"].replace("第 2 个测试点失败", "全部通过，无错误")
        connection.execute(
            "UPDATE ai_programming_tasks SET result_json=? WHERE task_id=?",
            (forged, task_id),
        )

    response = client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"
    assert stored_row(runtime, task_id)["state"] == "completed"


def test_revoked_scope_blocks_readback(client, runtime):
    assessment_id = upload(client)
    task_id = complete(client, runtime, assessment_id, "rb-scope")
    runtime.admin.remove_member(runtime.teacher, runtime.scope)

    response = client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_SCOPE_REVOKED"


def test_revoked_credentials_block_readback(client, runtime):
    assessment_id = upload(client)
    task_id = complete(client, runtime, assessment_id, "rb-token")
    runtime.admin.revoke_tokens(runtime.teacher)

    response = client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_queued_status_does_not_reread_archives(client, runtime):
    """轮询未完成的任务只读状态，不逐个解压 ZIP；原件缺失也不影响状态回读。"""
    assessment_id = upload(client)
    bound = anchors(runtime, assessment_id)
    service = client.app.state.programming_tasks
    service.runner = lambda *args, **kwargs: None
    row, _ = service.create(
        runtime.p, assessment_id, ProgrammerTaskCreate(idempotency_key="rb-queued")
    )
    original_path(client, bound["sha256"]).unlink()

    response = client.get(f"{URL}/{assessment_id}/ai-tasks/{row['task_id']}")
    assert response.status_code == 200
    assert response.json()["state"] == "queued"
    assert response.json()["result"] is None


def test_one_corrupted_package_blocks_the_whole_result(client, runtime):
    assessment_id = upload(
        client,
        [
            ("甲班.zip", package("S001", "周同学")),
            ("乙班.zip", package("S002", "李同学", verdict="答案错误")),
        ],
    )
    bound = anchors(runtime, assessment_id)
    task_id = complete(client, runtime, assessment_id, "rb-multi")
    assert len(bound["assets"]) == 2

    second = original_path(client, bound["assets"][1]["sha256"])
    second.write_bytes(second.read_bytes() + b"broken")

    response = client.get(f"{URL}/{assessment_id}/ai-tasks/{task_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_CHANGED"
    assert stored_row(runtime, task_id)["state"] == "completed"
