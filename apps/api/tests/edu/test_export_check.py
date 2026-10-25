"""Preflight shares download guards without building archives or changing facts."""

import hashlib
import json

import pytest

from .test_insights_api import formal_batch as formal_batch
from .test_reviewed_results import export_result, publish, save_result
from .test_reviewed_results import flow as flow


def check(client, base, batch, saved, **patch):
    return client.post(
        base + "/analysis-results/" + saved["result_id"] + "/export-check",
        json={"snapshot_id": batch["snapshot_id"], "expected_version": saved["version"], **patch},
    )


@pytest.mark.parametrize("format", ["json", "zip"])
def test_preflight_returns_counts_budget_warnings_without_archive_or_business_writes(
    client, runtime, flow, monkeypatch, format
):
    import app.edu.exports as exports

    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    before = runtime.db.path.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("Preflight must not generate archive or CSV")

    monkeypatch.setattr(exports, "ZipFile", forbidden)
    monkeypatch.setattr(exports, "csv_bytes", forbidden)
    response = check(client, base, batch, saved, format=format)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exportable"] is True and body["format"] == format
    assert body["member_count"] == 2 and body["confirmed_finding_count"] == 0
    assert 0 < body["json_payload_bytes"] < body["max_json_payload_bytes"] == 8 * 1024 * 1024
    warnings = {w["code"]: w["count"] for w in body["warnings"]}
    assert warnings == {
        "PENDING_FINDINGS_EXCLUDED": len(saved["findings"]),
        "WITHHELD_COMMENTS_EXCLUDED": 10,
        "WITHHELD_RUBRIC_EXCLUDED": 5,
    }
    assert "合成私密姓名" not in response.text and "未审核合成" not in response.text
    assert "evidence" not in body and "analysis" not in body
    assert response.headers["Cache-Control"] == "no-store"
    assert runtime.db.path.read_bytes() == before


def test_preflight_reads_current_text_and_download_rechecks_after_version_change(client, flow):
    base, batch, proof, _ = flow
    saved = save_result(client, base, batch).json()
    assert publish(client, base, batch, proof).status_code == 200
    first = check(client, base, batch, saved).json()
    warnings = {w["code"]: w["count"] for w in first["warnings"]}
    assert warnings["WITHHELD_COMMENTS_EXCLUDED"] == 9
    result = client.patch(
        base + "/analysis-results/" + saved["result_id"],
        json={"snapshot_id": batch["snapshot_id"], "expected_version": 1, "note": "重新核验"},
    )
    assert result.status_code == 200
    assert export_result(client, base, batch, saved).status_code == 409
    assert check(client, base, batch, saved).status_code == 409
    assert check(client, base, batch, result.json()).status_code == 200


def test_preflight_dismissed_confirmed_and_empty_group(client, flow):
    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    decisions = [
        {"candidate_id": f["candidate"]["candidate_id"], "status": status}
        for f, status in zip(saved["findings"], ["confirmed", "dismissed"], strict=False)
    ]
    saved = client.patch(
        base + "/analysis-results/" + saved["result_id"],
        json={"snapshot_id": batch["snapshot_id"], "expected_version": 1, "decisions": decisions},
    ).json()
    body = check(client, base, batch, saved).json()
    assert body["confirmed_finding_count"] == 1
    assert (
        next(w for w in body["warnings"] if w["code"] == "DISMISSED_FINDINGS_EXCLUDED")["count"]
        == 1
    )
    empty = save_result(
        client,
        base,
        batch,
        conditions=[{"dimension_code": "algorithm_data_structure", "operator": "lt", "value": 1}],
    ).json()
    assert check(client, base, batch, empty).json()["member_count"] == 0


def test_preflight_access_authentication_snapshot_and_version_guards(client, runtime, flow):
    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    assert check(client, base, batch, saved, snapshot_id="wrong").status_code == 409
    assert check(client, base, batch, saved, expected_version=2).status_code == 409
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    assert check(client, base, batch, saved).status_code == 404
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    assert check(client, base, batch, saved).status_code == 200
    runtime.admin.revoke_read(runtime.other, batch["batch_id"])
    assert check(client, base, batch, saved).status_code == 404
    runtime.admin.revoke_tokens(runtime.other)
    assert check(client, base, batch, saved).status_code == 401


@pytest.mark.parametrize(
    "patch",
    [
        {"expected_version": True},
        {"expected_version": "1"},
        {"expected_version": 0},
        {"format": "pdf"},
        {"extra": "private"},
    ],
)
def test_preflight_strict_input(client, flow, patch):
    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    response = check(client, base, batch, saved, **patch)
    assert response.status_code == 422
    assert "private" not in response.text


def test_preflight_and_export_have_identical_size_guard(client, flow, monkeypatch):
    import app.edu.exports as exports

    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    monkeypatch.setattr(exports, "MAX_EXPORT_BYTES", 100)
    for response in (check(client, base, batch, saved), export_result(client, base, batch, saved)):
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "EXPORT_TOO_LARGE"


@pytest.mark.parametrize(
    "corruption", ["hash", "score", "evidence", "dimension", "count", "duplicate"]
)
def test_preflight_and_export_reject_corrupt_saved_references(client, runtime, flow, corruption):
    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    with runtime.db.transaction(write=True) as connection:
        # Deliberately bypass the production immutability trigger in this disposable DB.
        connection.execute("DROP TRIGGER immutable_analysis_results_update")
        row = connection.execute(
            "SELECT payload_json FROM analysis_results WHERE result_id=?", (saved["result_id"],)
        ).fetchone()
        payload = json.loads(row[0])
        if corruption == "score":
            payload["members"][0]["scores"][0]["score"] = 4.99
        elif corruption == "evidence":
            payload["members"][0]["scores"][0]["evidence_id"] = "missing"
        elif corruption == "dimension":
            payload["members"][0]["scores"][0]["dimension_code"] = payload["members"][0]["scores"][
                1
            ]["dimension_code"]
        elif corruption == "count":
            payload["matched_count"] += 1
        elif corruption == "duplicate":
            payload["members"][1] = payload["members"][0]
        text = json.dumps(payload)
        digest = "0" * 64 if corruption == "hash" else hashlib.sha256(text.encode()).hexdigest()
        connection.execute(
            "UPDATE analysis_results SET payload_json=?,payload_sha256=? WHERE result_id=?",
            (text, digest, saved["result_id"]),
        )
    for response in (check(client, base, batch, saved), export_result(client, base, batch, saved)):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"


def test_preflight_auth_precedes_body_and_rechecks_after_decode(client, runtime, flow, monkeypatch):
    import app.routers.edu_support as routes

    base, batch, _, _ = flow
    saved = save_result(client, base, batch).json()
    original = routes.read_query

    async def revoke_after_decode(request, model):
        body = await original(request, model)
        runtime.admin.revoke_tokens(runtime.teacher)
        return body

    monkeypatch.setattr(routes, "read_query", revoke_after_decode)
    assert check(client, base, batch, saved).status_code == 401
    response = client.post(
        base + "/analysis-results/" + saved["result_id"] + "/export-check",
        content="secret invalid json",
    )
    assert response.status_code == 401 and "secret" not in response.text
