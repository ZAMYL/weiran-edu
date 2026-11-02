"""Formal HTTP exports from synthetic uploads, real saved results and temporary SQLite."""

import hashlib
import io
import json
import sqlite3
from datetime import timedelta
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from app.edu.results import ResultService
from app.edu.review_models import AnalysisPatch
from app.reporting.edu import ReportError

from .test_export_check import check
from .test_insights_api import CONDITIONS, A
from .test_insights_api import formal_batch as formal_batch
from .test_reviewed_results import export_result, publish, save_result
from .test_reviewed_results import flow as flow


def confirm(client, base, batch, saved):
    response = client.patch(
        base + "/analysis-results/" + saved["result_id"],
        json={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": saved["version"],
            "decisions": [
                {"candidate_id": f["candidate"]["candidate_id"], "status": "confirmed"}
                for f in saved["findings"]
                if f["candidate"]["candidate_type"] == "ability_group"
                or f["candidate"]["dimension_code"] == A
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def read_report(response, format):
    assert response.status_code == 200, (
        response.text[:200] if format == "md" else response.status_code
    )
    if format == "md":
        return None, response.content.decode("utf-8")
    workbook = load_workbook(io.BytesIO(response.content))
    text = "\n".join(
        str(c.value) for sheet in workbook for row in sheet for c in row if c.value is not None
    )
    return workbook, text


@pytest.mark.parametrize("size", [1, 17, 53, 105])
def test_four_formats_match_real_saved_counts_conditions_and_confirmations(
    client, formal_batch, size
):
    base, batch, _ = formal_batch(size)
    saved = confirm(client, base, batch, save_result(client, base, batch).json())
    legacy = export_result(client, base, batch, saved).json()
    archive = export_result(client, base, batch, saved, format="zip")
    assert archive.status_code == 200
    with ZipFile(io.BytesIO(archive.content)) as zipped:
        packed = json.loads(zipped.read("analysis.json"))
        assert packed["analysis"] == legacy["analysis"]
        assert packed["confirmed_findings"] == legacy["confirmed_findings"]
    matched = sum(i % 4 in (0, 1) for i in range(size))
    assert legacy["analysis"]["matched_count"] == matched
    assert legacy["analysis"]["total_count"] == size
    assert legacy["analysis"]["query"]["conditions"] == CONDITIONS
    confirmed_ids = {f["candidate"]["candidate_id"] for f in legacy["confirmed_findings"]}
    assert len(confirmed_ids) == 2
    for format, mime in (
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("md", "text/markdown; charset=utf-8"),
    ):
        before = check(client, base, batch, saved, format=format)
        assert before.status_code == 200, before.text
        preflight = before.json()
        response = export_result(client, base, batch, saved, format=format)
        assert response.headers["Content-Type"] == mime
        assert response.headers["Content-Disposition"] == (
            f'attachment; filename="weiran-analysis-{saved["result_id"]}-v2.{format}"'
        )
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Request-ID"].startswith("req_")
        assert int(response.headers["Content-Length"]) == len(response.content)
        assert preflight["report_payload_bytes"] == len(response.content)
        assert preflight["max_report_payload_bytes"] == 8 * 1024 * 1024
        assert preflight["member_count"] == matched and preflight["confirmed_finding_count"] == 2
        assert "仅汇总" in preflight["note"]
        workbook, text = read_report(response, format)
        assert str(size) in text and saved["result_id"] in text and batch["snapshot_id"] in text
        assert "算法与数据结构" in text and "解题策略" in text and "，且" in text
        assert "不证明因果关系" in text
        for f in saved["findings"]:
            candidate_id = f["candidate"]["candidate_id"]
            assert (candidate_id in text) == (candidate_id in confirmed_ids)
        if workbook:
            info = {
                row[0]: row[1]
                for row in workbook["测评概况"].iter_rows(values_only=True)
                if row and row[0]
            }
            assert info["参评人数"] == size
            assert "默认仅汇总" in text
            metrics = {
                row[0]: row[1]
                for row in workbook["选定群体"].iter_rows(values_only=True)
                if len(row) > 2
            }
            assert metrics["符合条件人数"] == matched and metrics["参评人数"] == size
            assert all(
                c.data_type != "f" and c.hyperlink is None
                for sheet in workbook
                for row in sheet
                for c in row
            )
            workbook.close()
        if size == 17:
            assert "49/17" in text and "9/17" in text
            if format == "md":
                assert "52.94%" in text
        assert "合成私密姓名" not in text and "000000000001" not in text
        assert "未审核合成" not in text and batch["file_sha256"] not in text
        for student in legacy["analysis"]["members"]:
            assert student["student_id"] not in text
            assert all(s["evidence_id"] not in text for s in student["scores"])
    # Legacy preflight shape stays intact; the extra report budgets are absent.
    assert "report_payload_bytes" not in check(client, base, batch, saved).json()


@pytest.mark.parametrize("format", ["xlsx", "md"])
def test_zero_confirmed_no_conditions_and_zero_matches_are_real_empty_states(client, flow, format):
    base, batch, _, _ = flow
    pending = save_result(client, base, batch, conditions=[]).json()
    workbook, text = read_report(export_result(client, base, batch, pending, format=format), format)
    assert "## 教师确认发现" not in text and "暂无教师已确认发现" not in text
    if workbook:
        assert "教师确认发现" not in workbook.sheetnames and "选定群体" not in workbook.sheetnames
    if workbook:
        workbook.close()
    empty = save_result(
        client, base, batch, conditions=[{"dimension_code": A, "operator": "lt", "value": 1}]
    ).json()
    assert empty["analysis"]["members"] == []
    response = export_result(client, base, batch, empty, format=format)
    workbook, text = read_report(response, format)
    assert "没有匹配记录" in text and "暂无教师已确认发现" not in text
    assert "均值0" not in text
    if workbook:
        workbook.close()
    assert check(client, base, batch, empty, format=format).json()["member_count"] == 0


@pytest.mark.parametrize("format", ["xlsx", "md"])
def test_auth_access_version_snapshot_and_revocation_guards(client, runtime, flow, format):
    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    for patch in ({"snapshot_id": "wrong"}, {"expected_version": 9}):
        assert export_result(client, base, batch, saved, format=format, **patch).status_code == 409
        assert check(client, base, batch, saved, format=format, **patch).status_code == 409
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    assert export_result(client, base, batch, saved, format=format).status_code == 404
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    assert export_result(client, base, batch, saved, format=format).status_code == 200
    runtime.admin.revoke_read(runtime.other, batch["batch_id"])
    assert check(client, base, batch, saved, format=format).status_code == 404
    assert export_result(client, base, batch, saved, format=format).status_code == 404
    runtime.admin.revoke_tokens(runtime.other)
    assert export_result(client, base, batch, saved, format=format).status_code == 401
    client.headers.pop("Authorization")
    assert export_result(client, base, batch, saved, format=format).status_code == 401


@pytest.mark.parametrize("operation", ["check", "download"])
@pytest.mark.parametrize("change", ["token", "grant", "scope", "version", "expiry"])
def test_generation_rechecks_current_authorization_and_version(
    client,
    runtime,
    flow,
    monkeypatch,
    operation,
    change,
):
    import app.edu.exports as exports

    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    actor = runtime.p
    if change in {"grant", "scope"}:
        runtime.admin.grant_read(runtime.other, batch["batch_id"])
        client.headers["Authorization"] = "Bearer " + runtime.other_token
        actor = runtime.q
    original = exports.generate_report

    def mutate_after_render(*args, **kwargs):
        files = original(*args, **kwargs)
        if change == "token":
            runtime.admin.revoke_tokens(runtime.teacher)
        elif change == "grant":
            runtime.admin.revoke_read(runtime.other, batch["batch_id"])
        elif change == "scope":
            runtime.admin.remove_member(runtime.other, runtime.scope)
        elif change == "expiry":
            runtime.clock[0] += timedelta(days=8)
        else:
            ResultService(runtime.db).patch(
                actor,
                batch["batch_id"],
                saved["result_id"],
                AnalysisPatch(
                    snapshot_id=batch["snapshot_id"], expected_version=1, note="新核验版本"
                ),
            )
        return files

    monkeypatch.setattr(exports, "generate_report", mutate_after_render)
    request = check if operation == "check" else export_result
    response = request(client, base, batch, saved, format="xlsx")
    expected = 409 if change == "version" else 404 if change in {"grant", "scope"} else 401
    assert response.status_code == expected, response.text
    assert response.headers["Content-Type"].startswith("application/json")
    assert "Content-Disposition" not in response.headers


@pytest.mark.parametrize("format", ["xlsx", "md"])
def test_preflight_and_download_share_report_size_guard_and_error_mapping(
    client, flow, monkeypatch, format
):
    import app.edu.exports as exports

    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    monkeypatch.setattr(exports, "MAX_REPORT_BYTES", 50)
    for request in (check, export_result):
        response = request(client, base, batch, saved, format=format)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "EXPORT_TOO_LARGE"
        assert "Content-Disposition" not in response.headers
    # Report budget does not change JSON/ZIP behavior.
    assert export_result(client, base, batch, saved).status_code == 200
    assert export_result(client, base, batch, saved, format="zip").status_code == 200


@pytest.mark.parametrize(
    "error,status",
    [
        (ReportError("unsafe_export_text"), 503),
        (RuntimeError("synthetic-private-internal-path"), 500),
    ],
)
def test_generation_failure_is_json_error_not_partial_file(
    client, flow, monkeypatch, error, status
):
    import app.edu.exports as exports

    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(exports, "generate_report", fail)
    response = export_result(client, base, batch, saved, format="xlsx")
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Content-Type"].startswith("application/json")
    assert "Content-Disposition" not in response.headers
    assert "unsafe_export_text" not in response.text and "synthetic-private" not in response.text


@pytest.mark.parametrize("corruption", ["hash", "mean", "candidate", "member", "rule"])
def test_checked_saved_facts_must_still_match_current_database(
    client, runtime, flow, monkeypatch, corruption
):
    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    with runtime.db.transaction(write=True) as connection:
        connection.execute("DROP TRIGGER immutable_analysis_results_update")
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM analysis_results WHERE result_id=?", (saved["result_id"],)
            ).fetchone()[0]
        )
        if corruption == "mean":
            payload["statistics"]["dimensions"][0]["mean"] = {
                "value": 5.0,
                "exact": {"numerator": 5, "denominator": 1},
                "display": "5.00",
            }
        elif corruption == "candidate":
            payload["candidates"][0]["metrics"][0]["source_record_count"] = 999
        elif corruption == "member":
            payload["members"] = payload["members"][:-1]
            payload["matched_count"] -= 1
        elif corruption == "rule":
            payload["candidates"][0]["rule_version"] = "unknown-version"
        text = json.dumps(payload)
        digest = "0" * 64 if corruption == "hash" else hashlib.sha256(text.encode()).hexdigest()
        connection.execute(
            "UPDATE analysis_results SET payload_json=?,payload_sha256=? WHERE result_id=?",
            (text, digest, saved["result_id"]),
        )
    for request in (check, export_result):
        response = request(client, base, batch, saved, format="xlsx")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"


def test_reports_never_read_private_columns_or_include_even_published_prose(
    client, runtime, flow, monkeypatch
):
    base, batch, proof, _ = flow
    assert (
        publish(client, base, batch, proof, text="合成已发布但报告仍不接入的正文").status_code
        == 200
    )
    saved = confirm(
        client, base, batch, save_result(client, base, batch, title="=1+1", note="@SUM(1,2)").json()
    )
    original = runtime.db._connect
    denied = {
        "student_name",
        "student_number",
        "comment_raw",
        "identity_cell_raw",
        "text_cells_json",
        "raw_xlsx",
    }

    def connect():
        connection = original()
        connection.set_authorizer(
            lambda action, table, column, db, trigger: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_READ and column in denied
                else sqlite3.SQLITE_OK
            )
        )
        return connection

    monkeypatch.setattr(runtime.db, "_connect", connect)
    for format in ("xlsx", "md"):
        response = export_result(client, base, batch, saved, format=format)
        workbook, text = read_report(response, format)
        assert "合成已发布但报告仍不接入的正文" not in text and runtime.token not in text
        assert "=1+1" not in text and "@SUM(1,2)" not in text
        if workbook:
            workbook.close()


def test_cross_batch_and_client_forgery_are_not_accepted(client, formal_batch):
    base, batch, _ = formal_batch()
    saved = save_result(client, base, batch).json()
    other_base, other_batch, _ = formal_batch(revision=batch["batch_id"], change=True)
    assert export_result(client, other_base, other_batch, saved, format="md").status_code == 404
    assert check(client, base, batch, saved, format="md", confirmed=True).status_code == 422
    assert export_result(client, base, batch, saved, format="pdf").status_code == 422
