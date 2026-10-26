"""Real FastAPI synthetic discovery → immutable save → versioned teacher decisions."""

import asyncio
import hashlib
import json
from dataclasses import replace
from fractions import Fraction
from io import BytesIO
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from app.edu import discovery_bridge, discovery_worker

from .test_discovery_api import custom_batch as custom_batch
from .test_discovery_api import query
from .test_insights_api import formal_batch as formal_batch


def save(client, base, batch, listed, **values):
    return client.post(
        base + "/analysis-results",
        json={
            "snapshot_id": batch["snapshot_id"],
            "conditions": listed["scope"]["scope_conditions"],
            "include_dimensions": False,
            "title": "合成发现分析",
            "note": "教师备注与系统事实分开",
            "discovery": {
                "input_key": listed["scope"]["input_key"],
                "discovery_ids": [f["discovery_id"] for f in listed["items"][:3]],
            },
            **values,
        },
    )


def test_paginated_candidates_save_complete_scope_and_original_charts(client, formal_batch):
    base, batch, _ = formal_batch(53)
    listed = query(client, base, batch, limit=1).json()
    saved = save(client, base, batch, listed)
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert body["findings"] == [] and len(body["discovery_findings"]) == 1
    assert body["analysis"]["matched_count"] == len(body["analysis"]["members"]) == 53
    mean = body["analysis"]["statistics"]["dimensions"][0]["mean"]["exact"]
    assert Fraction(mean["numerator"], mean["denominator"]) == Fraction(619, 212)
    finding = body["discovery_findings"][0]
    original = query(client, base, batch, finding["discovery"]["discovery_id"] + "/query").json()
    assert finding["discovery"] == original
    assert finding["teacher_review"]["status"] == "pending"
    assert client.get(base + "/analysis-results/" + body["result_id"]).json() == body
    assert "未审核合成" not in saved.text and "合成私密姓名" not in saved.text


def test_selection_requires_same_scope_and_never_accepts_client_facts(client, formal_batch):
    base, batch, _ = formal_batch(17)
    listed = query(client, base, batch).json()
    for values, expected in [
        ({"include_dimensions": True}, 422),
        ({"discovery": {"input_key": "0" * 64, "discovery_ids": []}}, 409),
        (
            {"discovery": {"input_key": listed["scope"]["input_key"], "discovery_ids": ["0" * 24]}},
            404,
        ),
        ({"snapshot_id": "stale"}, 409),
        ({"statistics": {}}, 422),
    ]:
        response = save(client, base, batch, listed, **values)
        assert response.status_code == expected, response.text
        assert response.headers["x-request-id"].startswith("req_")


def test_new_finding_review_is_versioned_and_readonly_cannot_write(client, runtime, formal_batch):
    base, batch, _ = formal_batch(17)
    listed = query(client, base, batch, limit=1).json()
    saved = save(client, base, batch, listed).json()
    path = base + "/analysis-results/" + saved["result_id"]
    decision = saved["discovery_findings"][0]["teacher_review"] | {"status": "confirmed"}
    patch = {"snapshot_id": batch["snapshot_id"], "expected_version": 1, "decisions": [decision]}
    changed = client.patch(path, json=patch)
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2
    assert changed.json()["analysis"] == saved["analysis"]
    assert changed.json()["discovery_findings"][0]["teacher_review"]["status"] == "confirmed"
    assert client.patch(path, json=patch).status_code == 409
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    assert client.get(path).status_code == 404
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    assert client.get(path).status_code == 200
    assert save(client, base, batch, listed).status_code == 403
    assert client.patch(path, json=patch | {"expected_version": 2}).status_code == 403
    runtime.admin.revoke_read(runtime.other, batch["batch_id"])
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("change", ["token", "member"])
def test_revoke_during_compute_persists_nothing(client, runtime, formal_batch, monkeypatch, change):
    base, batch, _ = formal_batch(17)
    listed = query(client, base, batch, limit=1).json()
    execute = discovery_worker.execute

    async def revoke(*args, **kwargs):
        output = await execute(*args, **kwargs)
        if change == "token":
            runtime.admin.revoke_tokens(runtime.teacher)
        else:
            runtime.admin.remove_member(runtime.teacher, runtime.scope)
        return output

    monkeypatch.setattr(discovery_worker, "execute", revoke)
    response = save(client, base, batch, listed)
    assert response.status_code == (401 if change == "token" else 404), response.text
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 0


def exported(client, base, batch, saved, format):
    return client.get(
        base + "/analysis-results/" + saved["result_id"] + "/export",
        params={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": saved["version"],
            "format": format,
        },
    )


def test_confirmed_new_discoveries_export_in_both_reports_and_private_formats(client, formal_batch):
    base, batch, _ = formal_batch(53)
    listed = query(client, base, batch, limit=3).json()
    saved = save(client, base, batch, listed).json()
    pending = exported(client, base, batch, saved, "md")
    assert pending.status_code == 200, pending.text
    assert "教师确认发现" not in pending.text.split("技术附录")[0]
    decisions = [
        {"candidate_id": f["discovery"]["discovery_id"], "status": status}
        for f, status in zip(
            saved["discovery_findings"], ["confirmed", "dismissed", "pending"], strict=False
        )
    ]
    saved = client.patch(
        base + "/analysis-results/" + saved["result_id"],
        json={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": 1,
            "decisions": decisions,
        },
    ).json()
    assert saved["version"] == 2
    for format in ("md", "xlsx", "json", "zip"):
        response = exported(client, base, batch, saved, format)
        assert response.status_code == 200, response.text[:200]
        assert response.headers["content-disposition"].endswith(f'v2.{format}"')
        if format == "md":
            assert "教师确认发现" in response.text
            body = response.text.split("技术附录")[0]
            assert "教师已确认发现数" in body
            assert all(
                marker not in body
                for marker in ("discovery_id", "rule_version", saved["snapshot_id"], "619/212")
            )
        elif format == "xlsx":
            workbook = load_workbook(BytesIO(response.content))
            assert "教师确认发现" in workbook.sheetnames
            assert all(cell.data_type != "f" for sheet in workbook for row in sheet for cell in row)
            counts = dict(
                (row[0], row[1])
                for row in workbook["测评概况"].values
                if len(row) > 1 and row[0] is not None
            )
            assert counts["教师已确认发现数"] == 1
            assert counts["测评记录数"] == 53
        else:
            if format == "zip":
                with ZipFile(BytesIO(response.content)) as archive:
                    payload = json.loads(archive.read("analysis.json"))
            else:
                payload = response.json()
            assert len(payload["confirmed_discoveries"]) == 1
            assert "discovery" not in payload["analysis"]
            assert "discovery_findings" not in payload["result"]
            assert payload["candidate_status_counts"] == {
                "pending": 1,
                "confirmed": 1,
                "dismissed": 1,
            }
    check = client.post(
        base + "/analysis-results/" + saved["result_id"] + "/export-check",
        json={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": 2,
            "format": "xlsx",
        },
    )
    assert check.status_code == 200, check.text
    assert check.json()["confirmed_finding_count"] == 1


def test_scoped_boundary_report_uses_original_denominator_and_empty_scope_can_save(
    client, formal_batch
):
    base, batch, _ = formal_batch(53)
    conditions = [{"dimension_code": "algorithm_data_structure", "operator": "lte", "value": 3}]
    listed = query(client, base, batch, scope_conditions=conditions, limit=50).json()
    boundary = next(f for f in listed["items"] if f["type"] == "boundary_sensitivity")
    listed["items"] = [boundary]
    saved = save(client, base, batch, listed).json()
    assert saved["analysis"]["matched_count"] == 40
    assert saved["analysis"]["discovery"]["scope"]["scope_record_count"] == 40
    saved = client.patch(
        base + "/analysis-results/" + saved["result_id"],
        json={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": 1,
            "decisions": [{"candidate_id": boundary["discovery_id"], "status": "confirmed"}],
        },
    ).json()
    report = exported(client, base, batch, saved, "md")
    assert report.status_code == 200, report.text
    assert "筛选边界对群体人数的影响" in report.text
    assert "40" in report.text and "13" in report.text
    empty = query(
        client, base, batch, scope_conditions=[conditions[0] | {"operator": "lt", "value": 1}]
    ).json()
    saved_empty = save(client, base, batch, empty)
    assert saved_empty.status_code == 201, saved_empty.text
    assert saved_empty.json()["analysis"]["members"] == []
    assert exported(client, base, batch, saved_empty.json(), "md").status_code == 200


def test_rehashed_corrupt_discovery_is_rejected_by_recomputation(client, runtime, formal_batch):
    base, batch, _ = formal_batch(17)
    saved = save(client, base, batch, query(client, base, batch, limit=1).json()).json()
    with runtime.db.transaction(write=True) as connection:
        connection.execute("DROP TRIGGER immutable_analysis_results_update")
        original = connection.execute(
            "SELECT payload_json FROM analysis_results WHERE result_id=?", (saved["result_id"],)
        ).fetchone()[0]
        payload = json.loads(original)
        payload["discovery"]["findings"][0]["title"] = "伪造但重新计算过摘要的标题"
        raw = json.dumps(payload, ensure_ascii=False)
        connection.execute(
            "UPDATE analysis_results SET payload_json=?,payload_sha256=? WHERE result_id=?",
            (raw, hashlib.sha256(raw.encode()).hexdigest(), saved["result_id"]),
        )
    response = exported(client, base, batch, saved, "md")
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"
    assert "content-disposition" not in response.headers


def test_save_hard_timeout_does_not_create_result(client, runtime, formal_batch, monkeypatch):
    from .test_discovery_worker import child_ids, sleeping_child

    base, batch, _ = formal_batch(17)
    listed = query(client, base, batch, limit=1).json()
    before = child_ids()
    monkeypatch.setattr(discovery_bridge, "MAX_SECONDS", 0.5)
    monkeypatch.setattr(discovery_worker, "compute_child", sleeping_child)
    response = save(client, base, batch, listed)
    assert response.status_code == 503 and response.json()["error"]["code"] == "DISCOVERY_TIMEOUT"
    assert child_ids() == before
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 0


def test_readonly_download_and_revoke_after_preflight(client, runtime, formal_batch):
    base, batch, _ = formal_batch(17)
    saved = save(client, base, batch, query(client, base, batch, limit=1).json()).json()
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    check = client.post(
        base + "/analysis-results/" + saved["result_id"] + "/export-check",
        json={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": 1,
            "format": "md",
        },
    )
    assert check.status_code == 200, check.text
    assert exported(client, base, batch, saved, "md").status_code == 200
    runtime.admin.revoke_read(runtime.other, batch["batch_id"])
    denied = exported(client, base, batch, saved, "md")
    assert denied.status_code == 404 and "content-disposition" not in denied.headers


@pytest.mark.parametrize("kind", ["dimension_relation", "group_difference", "rare_combination"])
def test_all_supported_discovery_reports_have_chinese_readable_metrics(
    client, formal_batch, custom_batch, kind
):
    if kind == "rare_combination":
        base, batch = custom_batch([(3, 3, 3, 3, 3)] * 16 + [(5, 1, 3, 3, 3)])
    elif kind == "dimension_relation":
        base, batch = custom_batch([(1, 5, 2, 3, 3)] * 8 + [(5, 1, 4, 3, 3)] * 9)
    else:
        base, batch, _ = formal_batch(53)
    listed = query(client, base, batch, limit=50).json()
    finding = next(f for f in listed["items"] if f["type"] == kind)
    listed["items"] = [finding]
    saved = save(client, base, batch, listed).json()
    saved = client.patch(
        base + "/analysis-results/" + saved["result_id"],
        json={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": 1,
            "decisions": [{"candidate_id": finding["discovery_id"], "status": "confirmed"}],
        },
    ).json()
    for format in ("xlsx", "md"):
        response = exported(client, base, batch, saved, format)
        assert response.status_code == 200, response.text[:200]
        if format == "md":
            body = response.text.split("技术附录")[0]
            assert "分析范围人数" in body and "教师已确认" in body
            assert kind not in body and finding["discovery_id"] not in body


def test_source_outside_batch_cannot_be_saved(client, runtime, formal_batch, monkeypatch):
    from app.edu import discovery_results

    base, batch, _ = formal_batch(17)
    listed = query(client, base, batch, limit=1).json()
    original = discovery_results.sources

    def forged(finding, key):
        actual = original(finding, key)
        return (
            ((replace(actual[0][0], student_id="outside"), actual[0][1]), *actual[1:])
            if actual
            else actual
        )

    monkeypatch.setattr(discovery_results, "sources", forged)
    response = save(client, base, batch, listed)
    assert response.status_code == 503, response.text
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 0


def test_asgi_save_disconnect_reaps_child_and_persists_nothing(
    client, runtime, formal_batch, monkeypatch
):
    from .test_discovery_worker import child_ids, sleeping_child

    base, batch, _ = formal_batch(17)
    listed = query(client, base, batch, limit=1).json()
    before = child_ids()
    execute = discovery_worker.execute
    monkeypatch.setattr(discovery_worker, "compute_child", sleeping_child)

    async def scenario():
        started = asyncio.Event()
        consumed = False

        async def running(*args, **kwargs):
            started.set()
            return await execute(*args, **kwargs)

        monkeypatch.setattr(discovery_worker, "execute", running)
        body = json.dumps(
            {
                "snapshot_id": batch["snapshot_id"],
                "include_dimensions": False,
                "title": "合成取消测试",
                "discovery": {
                    "input_key": listed["scope"]["input_key"],
                    "discovery_ids": [listed["items"][0]["discovery_id"]],
                },
            }
        ).encode()

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        path = base + "/analysis-results"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", ("Bearer " + runtime.token).encode()),
            ],
        }
        await asyncio.wait_for(client.app(scope, receive, send), timeout=8)

    asyncio.run(scenario())
    assert child_ids() == before
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 0
