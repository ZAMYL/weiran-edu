import pytest


def upload(client, runtime, book, pack, **fields):
    return client.post(
        "/api/v1/edu/imports/preview",
        data={"data_scope_id": runtime.scope, "title": "合成测评", **fields},
        files={
            "file": (
                "synthetic.xlsx",
                pack(book),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def commit(client, view, **fields):
    return client.post(
        f"/api/v1/edu/imports/{view['import']['import_id']}/commit",
        json={"preview_version": view["import"]["preview_version"], **fields},
    )


def test_http_end_to_end_upload_report_commit_batch_students_and_evidence(
    client, runtime, book, pack
):
    response = upload(client, runtime, book, pack)
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"].startswith("req_")
    view = response.json()
    assert view["import"]["status"] == "preview_ready"
    assert view["summary"]["parsed_evaluation_count"] == 10
    report = client.get(f"/api/v1/edu/imports/{view['import']['import_id']}/quality-report")
    assert report.status_code == 200 and report.json()["validation_passed"]
    markdown = client.get(
        f"/api/v1/edu/imports/{view['import']['import_id']}/quality-report?format=markdown"
    )
    assert "text/markdown" in markdown.headers["content-type"]
    committed = commit(client, view)
    assert committed.status_code == 201, committed.text
    batch_id = committed.json()["batch_id"]
    assert commit(client, view).status_code == 200
    assert commit(client, view).json()["result"] == "replayed"
    listed = client.get("/api/v1/edu/batches?limit=1").json()
    assert listed["total_count"] == 1 and len(listed["items"]) == 1
    assert client.get(f"/api/v1/edu/batches/{batch_id}").json()["evaluation_count"] == 10
    students = client.get(f"/api/v1/edu/batches/{batch_id}/students?limit=1").json()
    assert students["total_count"] == 2 and students["page"]["has_more"]
    student = students["items"][0]
    evidence = client.get(
        f"/api/v1/edu/batches/{batch_id}/students/{student['student_id']}/evidence"
    ).json()
    assert len(evidence["items"]) == 5
    one = client.get(
        f"/api/v1/edu/batches/{batch_id}/evidence/{student['scores'][0]['evidence_id']}"
    )
    assert one.json()["score"] == 2.75
    assert one.json()["comment"] is None and one.json()["comment_status"] == "withheld"
    assert one.json()["source"]["score_cell"] == "C3"
    for private in (
        "000000000001",
        "合成姓名0",
        "合成评语",
        "identity_cell_raw",
        "comment_raw",
        "owner_teacher_id",
    ):
        assert private not in str((view, listed, students, evidence, one.json()))
    cleared = client.get(f"/api/v1/edu/imports/{view['import']['import_id']}").json()
    assert not cleared["preview_available"] and cleared["rows"] is None


def test_batch_archive_is_account_scoped_and_reversible(client, runtime, book, pack):
    batch_id = commit(client, upload(client, runtime, book, pack).json()).json()["batch_id"]
    batches = "/api/v1/edu/batches"
    batch = f"{batches}/{batch_id}"
    archive = f"{batch}/archive"
    owner = client.headers["Authorization"]
    reader = "Bearer " + runtime.other_token

    assert client.patch(archive, json={"archived": "true"}).status_code == 422
    for _ in range(2):
        assert client.patch(archive, json={"archived": True}).json() == {"archived": True}
    assert client.get(batches).json()["total_count"] == 0
    assert client.get(batches + "?archived=true").json()["items"][0]["batch_id"] == batch_id
    assert client.get(batch).json()["archived"] is True
    assert client.get(batch + "/students").json()["total_count"] == 2

    client.headers["Authorization"] = reader
    assert client.patch(archive, json={"archived": True}).status_code == 404
    runtime.admin.set_member(runtime.other, runtime.scope, can_import=False)
    runtime.admin.grant_read(runtime.other, batch_id)
    assert client.get(batches).json()["total_count"] == 1
    assert client.get(batches + "?archived=true").json()["total_count"] == 0
    assert client.patch(archive, json={"archived": True}).status_code == 200

    client.headers["Authorization"] = owner
    assert client.patch(archive, json={"archived": False}).json() == {"archived": False}
    assert client.get(batch).json()["archived"] is False
    assert client.get(batches).json()["total_count"] == 1
    client.headers["Authorization"] = reader
    assert client.get(batches).json()["total_count"] == 0


def test_anonymous_upload_is_denied_before_reading_or_parsing(client):
    client.headers.pop("Authorization")
    response = client.post(
        "/api/v1/edu/imports/preview", content=b"not multipart", headers={"X-Teacher-ID": "forged"}
    )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "request_id" in response.json()["error"]


def test_other_teacher_cannot_read_preview_or_batch_and_404_matches_unknown(
    client, runtime, book, pack
):
    view = upload(client, runtime, book, pack).json()
    batch = commit(client, view).json()["batch_id"]
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    for path in (
        f"/imports/{view['import']['import_id']}",
        f"/batches/{batch}",
        "/batches/not-found",
    ):
        response = client.get("/api/v1/edu" + path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.parametrize(
    "body",
    [
        {"acknowledge_warnings": "true"},
        {"acknowledge_warnings": 1},
        {"teacher_id": "forged"},
        {"scores": [5, 5, 5, 5, 5]},
    ],
)
def test_commit_unknown_fields_and_non_boolean_ack_are_rejected(client, runtime, book, pack, body):
    view = upload(client, runtime, book, pack).json()
    response = commit(client, view, **body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_http_patch_and_quality_warning_confirmation(client, runtime, book, pack):
    book["Sheet2"]["D3"] = None
    view = upload(client, runtime, book, pack).json()
    assert commit(client, view).status_code == 409
    url = f"/api/v1/edu/imports/{view['import']['import_id']}"
    updated = client.patch(
        url, json={"preview_version": view["import"]["preview_version"], "title": "新合成标题"}
    )
    assert updated.status_code == 200
    assert (
        commit(client, view, acknowledge_warnings=True).json()["error"]["code"]
        == "PREVIEW_VERSION_MISMATCH"
    )
    assert commit(client, updated.json(), acknowledge_warnings=True).status_code == 201


def test_upload_rejects_impersonation_and_sensitive_error_echo(client, runtime, book, pack, caplog):
    response = upload(client, runtime, book, pack, teacher_id="private-sensitive-marker")
    assert response.status_code == 422
    assert (
        "private-sensitive-marker" not in response.text
        and "private-sensitive-marker" not in caplog.text
    )
    bad = client.post(
        "/api/v1/edu/imports/preview",
        data={"data_scope_id": runtime.scope, "title": "synthetic"},
        files={
            "file": (
                "private-sensitive-marker.xlsx",
                b"private-sensitive-marker",
                "application/octet-stream",
            )
        },
    )
    assert bad.status_code == 422
    assert "private-sensitive-marker" not in bad.text


def test_control_characters_in_metadata_are_rejected_without_echo(client, runtime, book, pack):
    response = upload(client, runtime, book, pack, title="合成\x00private-marker")
    assert response.status_code == 422
    assert "private-marker" not in response.text


def test_invalid_upload_still_gets_quality_preview_and_never_creates_batch(
    client, runtime, book, pack
):
    book["Sheet2"]["C3"] = "private-sensitive-marker"
    response = upload(client, runtime, book, pack)
    assert response.status_code == 201 and response.json()["import"]["status"] == "invalid"
    assert "private-sensitive-marker" not in response.text
    assert commit(client, response.json()).json()["error"]["code"] == "IMPORT_NOT_CONFIRMABLE"
    assert client.get("/api/v1/edu/batches").json()["total_count"] == 0


def test_snapshot_mismatch_pagination_and_openapi_upload_contract(client, runtime, book, pack):
    batch_id = commit(client, upload(client, runtime, book, pack).json()).json()["batch_id"]
    assert client.get(f"/api/v1/edu/batches/{batch_id}/students?snapshot_id=old").status_code == 409
    assert client.get(f"/api/v1/edu/batches/{batch_id}/students?limit=101").status_code == 422
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/edu/imports/preview"]["post"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert "multipart/form-data" in operation["requestBody"]["content"]


def test_actual_upload_byte_limit_does_not_trust_content_length(client):
    def body():
        for _ in range(12):
            yield b"x" * (1024 * 1024)

    response = client.post(
        "/api/v1/edu/imports/preview",
        content=body(),
        headers={"Content-Type": "multipart/form-data; boundary=synthetic"},
    )
    assert response.status_code == 413


def test_http_flow_with_real_isolated_parser(client, runtime, book, pack):
    from app.importing.edu.worker import parse_isolated

    runtime.service.parser = parse_isolated
    response = upload(client, runtime, book, pack)
    assert response.status_code == 201, response.text
    created = commit(client, response.json())
    assert created.status_code == 201, created.text
    assert (
        client.get(f"/api/v1/edu/batches/{created.json()['batch_id']}").json()["student_count"] == 2
    )
