"""Real FastAPI and disposable SQLite; entirely synthetic question workbook."""

import http.client
import json
import socket
import threading
import time
from datetime import timedelta
from fractions import Fraction

import pytest
import uvicorn

from app.core.errors import APIError

from .test_question_xlsx import synthetic

BASE = "/api/v1/edu"


def test_question_upload_confirm_statistics_evidence_and_revocation(client, runtime):
    raw = synthetic(lambda w: setattr(w["Sheet2"]["B2"], "value", None))
    response = client.post(
        BASE + "/imports/preview", files={"file": ("逐题.xlsx", raw, "application/octet-stream")}
    )
    assert response.status_code == 201, response.text
    preview = response.json()
    assert preview["participant_count"] == 20 and preview["observation_count"] == 260
    assert len(preview["recognized_items"]) == 13
    assert preview["quality_issues"][0]["category"] == "missing"
    path = BASE + f"/imports/{preview['import_id']}"
    assert client.get(path).status_code == 200
    assert client.get(path + "/quality-report").status_code == 200
    body = {"preview_version": preview["preview_version"]}
    assert client.post(path + "/commit", json=body).status_code == 409
    body["acknowledge_warnings"] = True
    committed = client.post(path + "/commit", json=body)
    assert committed.status_code == 201, committed.text
    ref = committed.json()
    assert client.post(path + "/commit", json=body).json()["result"] == "replayed"
    url = BASE + f"/batches/{ref['batch_id']}"
    result = client.get(url + "/statistics", params={"snapshot_id": ref["snapshot_id"]})
    assert result.status_code == 200, result.text
    stats = result.json()
    first = stats["items"][0]
    assert first["denominator"] == 20 and first["valid_count"] == 19
    assert first["missing_count"] == 1 and first["invalid_count"] == 0
    assert Fraction(**first["mean"]) == Fraction(11, 4)
    assert stats["analysis_status"]["five_dimension_discovery"] == "unsupported"
    evidence = client.get(url + "/evidence/" + first["evidence_ids"][1])
    assert evidence.status_code == 200 and evidence.json()["item_id"] == "Q01"
    assert evidence.json()["source"]["sheet"] == "Sheet2"
    assert client.get(url + "/statistics", params={"snapshot_id": "wrong"}).status_code == 409
    runtime.admin.set_member(runtime.other, runtime.scope, can_import=False)
    runtime.admin.grant_read(runtime.other, ref["batch_id"])
    headers = {"Authorization": "Bearer " + runtime.other_token}
    assert client.get(url + "/statistics", headers=headers).status_code == 200
    assert (
        client.post(
            BASE + "/imports/preview",
            headers=headers,
            files={"file": ("q.xlsx", raw, "application/octet-stream")},
        ).status_code
        == 403
    )
    runtime.admin.revoke_read(runtime.other, ref["batch_id"])
    assert client.get(url + "/statistics", headers=headers).status_code == 404
    runtime.admin.grant_read(runtime.other, ref["batch_id"])
    runtime.admin.remove_member(runtime.other, runtime.scope)
    runtime.admin.set_member(runtime.other, runtime.scope, can_import=False)
    assert client.get(url + "/statistics", headers=headers).status_code == 404
    with pytest.raises(APIError):
        with runtime.db.transaction(write=True) as connection:
            connection.execute("UPDATE question_observations SET raw_value='0'")
    with runtime.db.transaction(write=True) as connection:
        connection.execute("DROP TRIGGER question_observations_no_update")
        connection.execute("UPDATE question_observations SET raw_value='0' WHERE source_cell='B2'")
    assert client.get(url + "/statistics").status_code == 503
    runtime.admin.remove_member(runtime.teacher, runtime.scope)
    assert client.get(url + "/statistics").status_code == 404


def test_real_loopback_tcp_excel_sqlite_statistics_and_evidence(client):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(10)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(client.app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    connection = None
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.01)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        # Explicit literal-loopback connection keeps the suite's network guard enabled.
        connection.sock = socket.socket()
        connection.sock.settimeout(15)
        connection.sock.connect(("127.0.0.1", port))

        def request(method, path, body=None, content_type="application/json"):
            headers = {
                "Authorization": client.headers["Authorization"],
                "Content-Type": content_type,
            }
            connection.request(method, BASE + path, body=body, headers=headers)
            response = connection.getresponse()
            result = json.loads(response.read())
            assert response.status in (200, 201), result
            return result

        boundary = "edu-synthetic-boundary"
        raw = synthetic()
        upload = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                'filename="synthetic.xlsx"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + raw
            + f"\r\n--{boundary}--\r\n".encode()
        )
        preview = request(
            "POST", "/imports/preview", upload, f"multipart/form-data; boundary={boundary}"
        )
        ref = request(
            "POST",
            f"/imports/{preview['import_id']}/commit",
            json.dumps({"preview_version": preview["preview_version"]}),
        )
        path = f"/batches/{ref['batch_id']}"
        stats = request("GET", path + "/statistics?snapshot_id=" + ref["snapshot_id"])
        assert stats["participant_count"] == 20 and len(stats["items"]) == 13
        assert all(
            i["valid_count"] == 20 and Fraction(**i["mean"]) == Fraction(11, 4)
            for i in stats["items"]
        )
        evidence = request("GET", path + "/evidence/" + stats["items"][0]["evidence_ids"][0])
        assert evidence["source"]["cell"] == "B2" and evidence["exact"] == {
            "numerator": 11,
            "denominator": 4,
        }
    finally:
        if connection:
            connection.close()
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


def test_duplicate_stale_preview_invalid_values_and_batch_isolation(client, runtime):
    def upload(raw):
        response = client.post(
            BASE + "/imports/preview", files={"file": ("q.xlsx", raw, "application/octet-stream")}
        )
        assert response.status_code == 201, response.text
        return response.json()

    raw = synthetic(lambda w: setattr(w["Sheet2"]["B2"], "value", -1))
    preview = upload(raw)
    path = BASE + f"/imports/{preview['import_id']}"
    changed = client.patch(
        path, json={"preview_version": preview["preview_version"], "title": "修改名称"}
    )
    assert changed.status_code == 200
    assert (
        client.post(
            path + "/commit", json={"preview_version": preview["preview_version"]}
        ).status_code
        == 409
    )
    body = {"preview_version": changed.json()["preview_version"], "acknowledge_warnings": True}
    ref = client.post(path + "/commit", json=body).json()
    duplicate = upload(raw)
    repeated = client.post(
        BASE + f"/imports/{duplicate['import_id']}/commit",
        json={"preview_version": duplicate["preview_version"], "acknowledge_warnings": True},
    )
    assert repeated.status_code == 200 and repeated.json()["result"] == "duplicate"
    assert repeated.json()["batch_id"] == ref["batch_id"]
    stats = client.get(BASE + f"/batches/{ref['batch_id']}/statistics").json()
    first = stats["items"][0]
    assert first["invalid_count"] == 1 and first["valid_count"] == 19
    assert Fraction(**first["mean"]) == Fraction(11, 4)
    another = upload(synthetic())
    new = client.post(
        BASE + f"/imports/{another['import_id']}/commit",
        json={"preview_version": another["preview_version"]},
    ).json()
    assert (
        client.get(
            BASE + f"/batches/{new['batch_id']}/evidence/{first['evidence_ids'][0]}"
        ).status_code
        == 404
    )
    expiring = upload(synthetic())
    runtime.clock[0] += timedelta(hours=2)
    assert (
        client.post(
            BASE + f"/imports/{expiring['import_id']}/commit",
            json={"preview_version": expiring["preview_version"]},
        ).status_code
        == 410
    )


def test_missing_scale_is_rejected_without_any_staging_write(client, runtime):
    raw = synthetic(lambda w: setattr(w["Sheet1"]["C2"], "value", None))
    response = client.post(
        BASE + "/imports/preview", files={"file": ("q.xlsx", raw, "application/octet-stream")}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "QUESTION_SCALE_REQUIRED"
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM question_imports").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM question_batches").fetchone()[0] == 0


def test_no_valid_scores_is_unavailable_not_zero_mean(client):
    def clear(workbook):
        for row in workbook["Sheet2"].iter_rows(min_row=2, min_col=2):
            for cell in row:
                cell.value = None

    raw = synthetic(clear)
    preview = client.post(
        BASE + "/imports/preview", files={"file": ("q.xlsx", raw, "application/octet-stream")}
    ).json()
    ref = client.post(
        BASE + f"/imports/{preview['import_id']}/commit",
        json={"preview_version": preview["preview_version"], "acknowledge_warnings": True},
    ).json()
    stats = client.get(BASE + f"/batches/{ref['batch_id']}/statistics").json()
    assert stats["analysis_status"]["item_statistics"] == "unavailable"
    assert all(
        item["mean"] is None and item["missing_count"] == 20 and item["denominator"] == 20
        for item in stats["items"]
    )
