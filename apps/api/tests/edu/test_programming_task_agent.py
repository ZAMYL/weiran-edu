"""Formal routes, durable SQLite tasks and real C-01 using synthetic ZIPs."""

import json
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest
from prototypes.adaptive_assessment.live.demo import native_response
from prototypes.research_agent_v2.programming import INSPECT, OVERVIEW
from prototypes.research_agent_v2.programming_task_adapter import install_offline_runner

from app.edu.programming_tasks import ProgrammerTaskCreate, digest
from edu.answersheet_samples import paper_html, zip_of
from edu.test_c01_database_evidence import OTHER_PACKAGE_CODE, PRIVATE_CODE, upload
from edu.test_programming_tasks import URL, submit

LABEL = "private-synthetic-problem"
HEADER = (
    "<thead><tr><th>测试点</th><th>结果</th><th>测试点得分</th>"
    "<th>耗时</th><th>内存</th></tr></thead>"
)


def archive(part: int, *, unknown: bool = False) -> bytes:
    entries = {}
    for index in range(10):
        point_status = "无法识别" if unknown else "答案错误"
        rows = [("8" if part else "7", point_status, "0", "1 ms", "32 KB")]
        content = paper_html(
            key=f"S{part * 10 + index + 1:03}",
            name="synthetic-private-name",
            label=LABEL,
            rows=rows,
            verdict="评测中" if unknown else "答案错误",
            code="" if unknown else OTHER_PACKAGE_CODE if part else PRIVATE_CODE,
        ).replace("<tbody>", HEADER + "<tbody>")
        if unknown:
            content = content.replace("<pre class='box font-mono'></pre>", "").replace(
                "编译器: GXX", "编译器: GCC"
            )
        else:
            content = content.replace("编译器: GXX", "编译器: Python")
        entries[f"包/正式答卷/restricted-{index}.html"] = content
    return zip_of(entries)


def transport(*, inspect_all=False, exhaust=False, after_first=None):
    decisions = []

    def mock(request):
        wire = request.content.decode()
        assert all(
            marker not in wire
            for marker in (
                PRIVATE_CODE,
                OTHER_PACKAGE_CODE,
                "61302",
                "82490",
                "restricted-",
                "S001",
                LABEL,
                "synthetic-private-name",
            )
        )
        facts = json.loads(json.loads(wire)["messages"][-1]["content"])["facts"]
        if not facts:
            decision = {"name": OVERVIEW, "arguments": {}}
        else:
            overview = facts[0]
            inspected = {fact["direction"]["direction"] for fact in facts[1:]}
            directions = [
                item for item in overview["directions"] if item["direction"] not in inspected
            ]
            if exhaust or (directions and (inspect_all or len(facts) == 1)):
                overview_id = overview["fact_id"]
                decision = {
                    "name": INSPECT,
                    "arguments": {
                        "overview_id": overview_id,
                        "direction": f"D99{len(decisions)}"
                        if exhaust
                        else directions[0]["direction"],
                    },
                    "basis_fact_ids": [overview_id],
                }
            else:
                decision = {
                    "name": "finish",
                    "arguments": {
                        "selected_fact_ids": [fact["fact_id"] for fact in facts[1:]],
                        "reason": "completed" if len(facts) > 1 else "insufficient_data",
                    },
                }
        decisions.append(decision)
        if len(decisions) == 1 and after_first:
            after_first()
        return httpx.Response(200, json=native_response(decision, len(decisions)))

    return httpx.MockTransport(mock), decisions


@pytest.mark.parametrize("multi", [False, True], ids=["single", "same-member-paths"])
def test_formal_programming_task_persists_verified_c01_result(client_factory, runtime, multi):
    payloads = (archive(0), archive(1)) if multi else (archive(0),)
    mock, decisions = transport(inspect_all=multi)
    with client_factory(runtime.token) as client:
        assessment = upload(client, *payloads)
        service = client.app.state.programming_tasks
        install_offline_runner(service, mock)
        response, view = submit(client, assessment, "c01-check")
        assert response.status_code == 202, response.text
        assert view["state"] == "completed", view
        assert (view["execution_origin"], view["real_qwen"], view["production_enabled"]) == (
            "http_mock",
            "not_run",
            False,
        )
        expected = [OVERVIEW, INSPECT] + ([INSPECT] if multi else []) + ["finish"]
        assert [decision["name"] for decision in decisions] == expected
        assert (view["model_requests"], view["tool_attempts"]) == (len(expected), len(expected) - 1)
        duplicate, repeated = submit(client, assessment, "c01-check")
        assert duplicate.status_code == 200 and repeated == view
        assert len(decisions) == len(expected)
        second, _ = submit(client, assessment, "another-campaign")
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "AI_CAMPAIGN_ALREADY_USED"

    task_id = view["task_id"]
    with runtime.db.transaction() as connection:
        saved = connection.execute(
            "SELECT result_json,result_sha,campaign_id FROM ai_programming_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        observed = {
            (row["source_asset_id"], row["external_key"], row["test_index"]): row
            for row in connection.execute(
                "SELECT s.source_asset_id,s.source_entry,s.source_index,p.external_key,"
                "t.test_index,t.source_row,t.status FROM submissions s "
                "JOIN assessment_participants p ON p.student_pk=s.student_pk "
                "AND p.assessment_id=s.assessment_id "
                "JOIN test_observations t ON t.submission_id=s.submission_id "
                "WHERE s.assessment_id=?",
                (assessment,),
            )
        }
    assert json.loads(saved["result_json"]) == view["result"]
    assert saved["result_sha"] == digest(view["result"])
    journal = runtime.db.path.parent / "programming-campaigns" / (saved["campaign_id"] + ".jsonl")
    last = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])
    assert last["request_count"] == len(expected)
    assert len(list(journal.parent.glob("*.jsonl"))) == 1

    with client_factory(runtime.token) as restarted:
        reopened = restarted.get(f"{URL}/{assessment}/ai-tasks/{task_id}")
        assert reopened.status_code == 200
        assert reopened.json() == view
        reader = restarted.app.state.assessment_reader
        assets = reader.overview(runtime.p, assessment).assets
        assert view["result"]["asset_sha256"] == [sha256(data).hexdigest() for data in payloads]
        assert len(view["result"]["findings"]) == len(payloads)
        for finding in view["result"]["findings"]:
            assert finding["code"] == "wrong_answer" and len(finding["evidence"]) == 10
            point = finding["evidence"][0]["test_index"]
            assert point in ({7, 8} if multi else {7})
            if point == 7:
                assert "indexing 10 / 10" in finding["statement"]
            else:
                assert "indexing 0 / 10" in finding["statement"]
            index = point - 7
            asset = assets[index]
            artifact = reader.original(runtime.p, assessment, asset.asset_id)
            assert artifact.payload == payloads[index] and artifact.sha256 == asset.sha256
            with ZipFile(BytesIO(artifact.payload)) as raw:
                for evidence in finding["evidence"]:
                    assert evidence["asset_id"] == asset.asset_id
                    assert evidence["test_index"] == point and evidence["source_row"] is not None
                    assert evidence["problem_label"] == LABEL and evidence["source_index"] == 0
                    row = observed[evidence["asset_id"], evidence["student_key"], point]
                    assert (row["source_entry"], row["source_index"], row["source_row"]) == (
                        evidence["source_entry"],
                        evidence["source_index"],
                        evidence["source_row"],
                    )
                    assert row["status"] == "答案错误"
                    member = raw.read(evidence["source_entry"])
                    assert evidence["student_key"].encode() in member
                    assert (OTHER_PACKAGE_CODE if index else PRIVATE_CODE).encode() in member
    assert len(decisions) == len(expected)


@pytest.mark.parametrize("damage", ["tampered", "missing"])
def test_completed_task_get_rejects_invalid_original_after_restart(client_factory, runtime, damage):
    mock, decisions = transport()
    with client_factory(runtime.token) as client:
        assessment = upload(client, archive(0))
        service = client.app.state.programming_tasks
        install_offline_runner(service, mock)
        response, view = submit(client, assessment, "c01-completed-original")
        assert response.status_code == 202
        assert view["state"] == "completed" and view["result"]["findings"]
        original = service.originals.path_for(view["result"]["asset_sha256"][0])

    path = f"{URL}/{assessment}/ai-tasks/{view['task_id']}"
    with client_factory(runtime.token) as restarted:
        assert restarted.get(path).json() == view
        if damage == "tampered":
            original.write_bytes(b"changed synthetic ZIP")
        else:
            original.unlink()
        invalid = restarted.get(path)
        assert invalid.status_code == 409
        assert invalid.json()["error"]["code"] == "ORIGINAL_UNAVAILABLE"
        assert invalid.json().get("result") is None
    assert len(decisions) == 3


def test_formal_programming_task_keeps_unknown_evidence_unknown(client):
    assessment = upload(client, archive(0, unknown=True))
    mock, decisions = transport()
    install_offline_runner(client.app.state.programming_tasks, mock)
    response, view = submit(client, assessment, "c01-insufficient")
    assert response.status_code == 202
    assert view["state"] == "completed" and view["result"]["findings"] == []
    assert [decision["name"] for decision in decisions] == [OVERVIEW, "finish"]
    assert "未支持" in view["result"]["statement"]


def test_formal_programming_task_budget_exhaustion_has_no_result(client, runtime):
    assessment = upload(client, archive(0))
    mock, decisions = transport(exhaust=True)
    install_offline_runner(client.app.state.programming_tasks, mock)
    response, view = submit(client, assessment, "c01-exhaust")
    assert response.status_code == 202
    assert view["state"] == "failed" and view["result"] is None
    assert view["error_code"] == "AI_TOOL_BUDGET_EXHAUSTED"
    assert (view["model_requests"], view["tool_attempts"], len(decisions)) == (6, 5, 6)
    duplicate, repeated = submit(client, assessment, "c01-exhaust")
    assert duplicate.status_code == 200 and repeated == view
    assert len(decisions) == 6
    with runtime.db.transaction() as connection:
        persisted = connection.execute(
            "SELECT result_sha FROM ai_programming_tasks WHERE task_id=?", (view["task_id"],)
        ).fetchone()
    assert persisted["result_sha"] is None


@pytest.mark.parametrize("when", ["queued", "running"])
@pytest.mark.parametrize(
    "change", ["credential", "teacher", "scope", "cancel", "deadline", "original"]
)
def test_formal_programming_task_stops_on_guard_changes(client, runtime, when, change):
    assessment = upload(client, archive(0))
    service = client.app.state.programming_tasks
    task_id = None

    def invalidate():
        if change == "credential":
            runtime.admin.revoke_tokens(runtime.p.teacher_id)
        elif change == "teacher":
            runtime.admin.set_active(runtime.p.teacher_id, False)
        elif change == "scope":
            runtime.admin.remove_member(runtime.p.teacher_id, runtime.scope)
        elif change == "cancel":
            assert task_id is not None
            service.cancel(runtime.p, task_id)
        elif change == "deadline":
            service.clock = lambda: row["deadline"] + 1
        else:
            service.originals.path_for(sha).write_bytes(b"tampered test original")

    sha = client.app.state.assessment_reader.overview(runtime.p, assessment).assets[0].sha256
    mock, decisions = transport(after_first=invalidate if when == "running" else None)
    install_offline_runner(service, mock)
    row, fresh = service.create(
        runtime.p, assessment, ProgrammerTaskCreate(idempotency_key="guard")
    )
    assert fresh
    task_id = row["task_id"]
    if when == "queued":
        invalidate()
    service.execute(task_id)

    assert len(decisions) == (1 if when == "running" else 0)
    with runtime.db.transaction() as connection:
        current = service._row(connection, task_id)
    assert current["state"] != "completed" and current["result_json"] is None
    assert not any(event["phase"] == "result_committed" for event in service.events(task_id))


def test_install_rejects_production_and_non_mock_transport(client):
    service = client.app.state.programming_tasks
    with httpx.HTTPTransport() as transport:
        with pytest.raises(ValueError, match="offline_programming_test_host_required"):
            install_offline_runner(service, transport)
    service.environment = "production"
    with pytest.raises(ValueError, match="offline_programming_test_host_required"):
        install_offline_runner(service, httpx.MockTransport(lambda _: httpx.Response(500)))
    assert service.runner is None
