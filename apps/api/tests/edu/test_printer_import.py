"""Synthetic printing uploads retain original byte hash and provenance through confirmation."""

import hashlib

import pytest

from tests.importing.printer_fixture import PART, devmode, pack_parts, printing, unpack

from .test_http import commit, upload


def test_printing_upload_confirm_and_evidence_keep_original_bytes(client, runtime, book, pack):
    original = pack(book)
    printed = printing(original, private=b"SYNTHETIC_PRIVATE_DRIVER_BYTES")
    preview = client.post(
        "/api/v1/edu/imports/preview",
        data={"data_scope_id": runtime.scope, "title": "纯合成打印设置兼容"},
        files={
            "file": (
                "synthetic.xlsx",
                printed,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert preview.status_code == 201, preview.text
    body = preview.json()
    assert body["import"]["file_sha256"] == hashlib.sha256(printed).hexdigest()
    done = commit(client, body, acknowledge_warnings=True)
    assert done.status_code == 201, done.text
    batch_id = done.json()["batch_id"]
    base = "/api/v1/edu/batches/" + batch_id
    batch = client.get(base).json()
    assert batch["file_sha256"] == hashlib.sha256(printed).hexdigest()
    students = client.get(base + "/students").json()
    assert students["total_count"] == 2
    evidence_id = students["items"][0]["scores"][0]["evidence_id"]
    proof = client.get(base + "/evidence/" + evidence_id).json()
    assert proof["source"]["file_sha256"] == batch["file_sha256"]
    assert proof["source"]["score_cell"].startswith("C")
    with runtime.db.transaction() as connection:
        assert (
            connection.execute(
                "SELECT raw_xlsx FROM batch_sources WHERE batch_id=?", (batch_id,)
            ).fetchone()[0]
            == printed
        )
    # Adding print metadata changes file bytes but not scoring content or duplicate identity.
    other = upload(client, runtime, book, pack)
    assert other.status_code == 201
    assert other.json()["import"]["content_sha256"] == body["import"]["content_sha256"]
    assert other.json()["import"]["file_sha256"] != body["import"]["file_sha256"]


@pytest.mark.parametrize("payload", [b"UNKNOWN_BINARY", b"MZ" + bytes(400)], ids=["unknown", "PE"])
def test_invalid_print_payload_has_safe_fixed_http_error(client, runtime, book, pack, payload):
    parts = unpack(printing(pack(book)))
    parts[PART] = payload
    response = client.post(
        "/api/v1/edu/imports/preview",
        data={"data_scope_id": runtime.scope, "title": "纯合成非法打印设置"},
        files={
            "file": (
                "synthetic.xlsx",
                pack_parts(parts),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "UNSUPPORTED_PRINTER_SETTINGS_FORMAT"
    assert "printerSettings1.bin" not in response.text and "UNKNOWN_BINARY" not in response.text
    assert client.get("/api/v1/edu/batches").json()["total_count"] == 0


@pytest.mark.parametrize(
    "case,expected_status,expected_code",
    [
        ("size", 413, "PRINTER_SETTINGS_SIZE_LIMIT"),
        ("orphan", 422, "INVALID_PRINTER_SETTINGS_PACKAGE"),
    ],
)
def test_print_limits_and_dangling_graph_have_clear_fixed_http_failures(
    client,
    runtime,
    book,
    pack,
    case,
    expected_status,
    expected_code,
):
    parts = unpack(printing(pack(book)))
    if case == "size":
        parts[PART] = devmode(b"x" * (64 * 1024 - 220 + 1))
    else:
        del parts[PART]
    response = client.post(
        "/api/v1/edu/imports/preview",
        data={"data_scope_id": runtime.scope, "title": "纯合成打印拒绝"},
        files={
            "file": (
                "synthetic.xlsx",
                pack_parts(parts),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert "原文件未被修改" in response.json()["error"]["message"]
    assert "printerSettings1.bin" not in response.text
