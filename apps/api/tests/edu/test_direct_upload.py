"""Real HTTP acceptance for existing files; no new layouts or model inference."""

from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile, ZipInfo

import pytest

from app.importing.edu import parse_excel


def upload(client, book, pack, *, filename="合成测评.xlsx", **fields):
    return client.post(
        "/api/v1/edu/imports/preview",
        data=fields,
        files={"file": (filename, pack(book), "application/octet-stream")},
    )


def no_writes(runtime):
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM imports").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM batches").fetchone()[0] == 0


def forbidden_parser(_data):
    raise AssertionError("Unauthorized or ambiguous uploads must not reach parsing")


def test_file_only_real_isolated_parse_edit_commit_statistics_and_evidence(
    client, runtime, book, pack
):
    from app.importing.edu.worker import parse_isolated

    runtime.service.parser = parse_isolated
    raw = pack(book)
    preview = client.post(
        "/api/v1/edu/imports/preview",
        files={"file": ("合成课程测评.xlsx", raw, "application/octet-stream")},
    )
    assert preview.status_code == 201, preview.text
    view = preview.json()
    info = view["import"]
    assert info["title"] == "合成课程测评"
    assert info["data_scope_id"] == runtime.scope
    assert info["course"] is None and info["assessment_date"] is None
    assert info["file_sha256"] == sha256(raw).hexdigest()
    assert view["summary"]["parsed_evaluation_count"] == 10
    quality = client.get(f"/api/v1/edu/imports/{info['import_id']}/quality-report")
    assert quality.status_code == 200 and quality.json()["validation_passed"]
    patched = client.patch(
        f"/api/v1/edu/imports/{info['import_id']}",
        json={"preview_version": info["preview_version"], "title": "教师修改后的测评名称"},
    )
    assert patched.status_code == 200
    commit_url = f"/api/v1/edu/imports/{info['import_id']}/commit"
    stale = client.post(commit_url, json={"preview_version": info["preview_version"]})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "PREVIEW_VERSION_MISMATCH"
    body = {"preview_version": patched.json()["import"]["preview_version"]}
    created = client.post(commit_url, json=body)
    assert created.status_code == 201
    assert client.post(commit_url, json=body).json()["result"] == "replayed"
    batch_id = created.json()["batch_id"]
    batch = client.get(f"/api/v1/edu/batches/{batch_id}").json()
    assert batch["title"] == "教师修改后的测评名称"
    assert batch["student_count"] == 2 and batch["evaluation_count"] == 10
    statistics = client.get(f"/api/v1/edu/batches/{batch_id}/statistics")
    assert statistics.status_code == 200
    students = client.get(f"/api/v1/edu/batches/{batch_id}/students").json()["items"]
    evidence = client.get(
        f"/api/v1/edu/batches/{batch_id}/evidence/{students[0]['scores'][0]['evidence_id']}"
    )
    assert evidence.status_code == 200 and evidence.json()["score"] == 2.75
    assert evidence.json()["comment"] is None
    with runtime.db.transaction() as connection:
        stored = connection.execute(
            "SELECT raw_xlsx FROM batch_sources WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        assert stored == raw
    duplicate = upload(client, book, pack)
    repeated = client.post(
        f"/api/v1/edu/imports/{duplicate.json()['import']['import_id']}/commit",
        json={"preview_version": duplicate.json()["import"]["preview_version"]},
    )
    assert repeated.status_code == 200 and repeated.json()["result"] == "duplicate"
    assert repeated.json()["batch_id"] == batch_id


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("C:\\private\\folder\\合成测评.XLSX", "合成测评"),
        ("/private/folder/合成测评.xlsx", "合成测评"),
        ("合成\u202e测评.xlsx", "合成测评"),
        (".xlsx", "未命名测评"),
        ("合" * 120 + ".xlsx", "合" * 100),
    ],
)
def test_default_title_is_bounded_basename(client, book, pack, filename, expected):
    response = upload(client, book, pack, filename=filename)
    assert response.status_code == 201, response.text
    assert response.json()["import"]["title"] == expected


def test_explicit_existing_fields_remain_authoritative(client, runtime, book, pack):
    response = upload(
        client,
        book,
        pack,
        title="教师指定名称",
        data_scope_id=runtime.scope,
        course="合成课程",
        assessment_date="2026-10-27",
    )
    assert response.status_code == 201
    info = response.json()["import"]
    assert (info["title"], info["course"], info["assessment_date"]) == (
        "教师指定名称",
        "合成课程",
        "2026-10-27",
    )


def test_multiple_scopes_need_context_and_never_pick_first(client, runtime, book, pack):
    other_scope = runtime.admin.create_scope("合成第二教学空间")
    runtime.admin.set_member(runtime.teacher, other_scope, can_import=True)
    runtime.service.parser = forbidden_parser
    response = upload(client, book, pack)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IMPORT_SCOPE_REQUIRED"
    assert runtime.scope not in response.text and other_scope not in response.text
    no_writes(runtime)
    runtime.service.parser = parse_excel
    selected = upload(client, book, pack, data_scope_id=other_scope)
    assert selected.status_code == 201
    assert selected.json()["import"]["data_scope_id"] == other_scope


def test_readonly_scope_does_not_confuse_unique_import_scope(client, runtime, book, pack):
    readonly = runtime.admin.create_scope("合成只读教学空间")
    runtime.admin.set_member(runtime.teacher, readonly, can_import=False)
    response = upload(client, book, pack)
    assert response.status_code == 201
    assert response.json()["import"]["data_scope_id"] == runtime.scope


@pytest.mark.parametrize("membership", ["none", "readonly"])
def test_no_import_permission_denies_before_parsing(client, runtime, book, pack, membership):
    if membership == "none":
        runtime.admin.remove_member(runtime.teacher, runtime.scope)
    else:
        runtime.admin.set_member(runtime.teacher, runtime.scope, can_import=False)
    runtime.service.parser = forbidden_parser
    response = upload(client, book, pack)
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    no_writes(runtime)


def test_explicit_unauthorized_scope_does_not_fall_back(client, runtime, book, pack):
    denied = runtime.admin.create_scope("合成无权限空间")
    runtime.service.parser = forbidden_parser
    response = upload(client, book, pack, data_scope_id=denied)
    assert response.status_code == 403
    no_writes(runtime)


@pytest.mark.parametrize("phase", ["resolved", "parsed"])
@pytest.mark.parametrize("revocation", ["token", "scope"])
def test_revocation_after_defaults_never_persists(
    client, runtime, book, pack, monkeypatch, phase, revocation
):
    def revoke():
        if revocation == "token":
            runtime.admin.revoke_tokens(runtime.teacher)
        else:
            runtime.admin.remove_member(runtime.teacher, runtime.scope)

    if phase == "resolved":
        from app.routers import edu

        original = edu.resolve_upload_metadata

        def resolved(*args, **kwargs):
            value = original(*args, **kwargs)
            revoke()
            return value

        monkeypatch.setattr(edu, "resolve_upload_metadata", resolved)
        runtime.service.parser = forbidden_parser
    else:

        def parsed(data):
            result = parse_excel(data)
            revoke()
            return result

        runtime.service.parser = parsed
    response = upload(client, book, pack)
    assert response.status_code == (401 if revocation == "token" else 403)
    no_writes(runtime)


def test_revision_defaults_to_owned_target_scope_with_multiple_memberships(
    client, runtime, book, pack
):
    first = upload(client, book, pack).json()["import"]
    committed = client.post(
        f"/api/v1/edu/imports/{first['import_id']}/commit",
        json={"preview_version": first["preview_version"]},
    )
    batch_id = committed.json()["batch_id"]
    second = runtime.admin.create_scope("合成第二空间")
    runtime.admin.set_member(runtime.teacher, second, can_import=True)
    book["Sheet2"]["C3"] = 3
    revised = upload(client, book, pack, revision_of_batch_id=batch_id)
    assert revised.status_code == 201
    assert revised.json()["import"]["data_scope_id"] == runtime.scope
    view = revised.json()["import"]
    created = client.post(
        f"/api/v1/edu/imports/{view['import_id']}/commit",
        json={"preview_version": view["preview_version"]},
    )
    assert created.status_code == 201
    assert not client.get(f"/api/v1/edu/batches/{batch_id}").json()["is_current"]


def test_shared_revision_cannot_supply_owner_context(client, runtime, book, pack):
    first = upload(client, book, pack).json()["import"]
    batch_id = client.post(
        f"/api/v1/edu/imports/{first['import_id']}/commit",
        json={"preview_version": first["preview_version"]},
    ).json()["batch_id"]
    runtime.admin.grant_read(runtime.other, batch_id)
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    runtime.service.parser = forbidden_parser
    response = upload(client, book, pack, revision_of_batch_id=batch_id)
    assert response.status_code == 404


def test_missing_score_is_real_invalid_preview_not_successful_batch(client, book, pack):
    book["Sheet2"]["C3"] = None
    response = upload(client, book, pack)
    assert response.status_code == 201 and response.json()["import"]["status"] == "invalid"
    assert "SCORE_REQUIRED" in {i["code"] for i in response.json()["issues"]}
    view = response.json()["import"]
    commit = client.post(
        f"/api/v1/edu/imports/{view['import_id']}/commit",
        json={"preview_version": view["preview_version"]},
    )
    assert commit.status_code == 409
    assert client.get("/api/v1/edu/batches").json()["total_count"] == 0


def test_empty_comments_keep_warning_confirmation(client, book, pack):
    book["Sheet2"]["D3"] = None
    response = upload(client, book, pack)
    assert response.status_code == 201
    view = response.json()["import"]
    path = f"/api/v1/edu/imports/{view['import_id']}/commit"
    body = {"preview_version": view["preview_version"]}
    assert client.post(path, json=body).json()["error"]["code"] == "WARNINGS_NOT_ACKNOWLEDGED"
    assert client.post(path, json={**body, "acknowledge_warnings": True}).status_code == 201


@pytest.mark.parametrize("items", [3, 5, 8, 13])
def test_unknown_flat_layout_is_explicitly_unsupported(client, runtime, book, pack, items):
    book.remove(book["Sheet1"])
    book.active.title = "测评评分"
    book.active.delete_rows(1, book.active.max_row)
    book.active.append(["匿名编号"] + [f"项目{i}" for i in range(items)])
    book.active.append(["合成001"] + [80] * items)
    response = upload(client, book, pack)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IMPORT_FORMAT_UNSUPPORTED"
    assert "暂不支持" in response.json()["error"]["message"]
    no_writes(runtime)


def zip_with_html() -> bytes:
    """造一个只含 HTML 的 ZIP：内容确实是 zip_html，不是 xlsx。"""
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(
            ZipInfo("index.html", date_time=(1980, 1, 1, 0, 0, 0)),
            "<html><body>合成答题卡</body></html>",
        )
    return stream.getvalue()


HTML_DOC = b"<!doctype html><html><body>synthetic</body></html>"
PDF_DOC = b"%PDF-1.7\n% synthetic\n"


@pytest.mark.parametrize(
    ("filename", "payload", "kind"),
    [
        pytest.param("合成资料.zip", zip_with_html(), "zip_html", id="zip_html"),
        pytest.param("合成资料.html", HTML_DOC, "html", id="html"),
        pytest.param("合成资料.pdf", PDF_DOC, "pdf", id="pdf"),
    ],
)
def test_recognised_containers_without_adapter_are_rejected(
    client, runtime, filename, payload, kind
):
    # 认得出类型，但没有对应解析器：必须明确报错，且绝不进入解析。
    runtime.service.parser = forbidden_parser
    response = client.post(
        "/api/v1/edu/imports/preview",
        files={"file": (filename, payload, "application/octet-stream")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_ADAPTER_FOR_ASSET"
    assert kind in response.json()["error"]["message"]
    no_writes(runtime)


def test_extension_cannot_disguise_a_zip_as_xlsx(client, runtime):
    # 文件名和 Content-Type 都伪装成 xlsx，内容却是 HTML 压缩包：按内容判定。
    runtime.service.parser = forbidden_parser
    response = client.post(
        "/api/v1/edu/imports/preview",
        files={
            "file": (
                "伪装成表格.xlsx",
                zip_with_html(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_ADAPTER_FOR_ASSET"
    assert "zip_html" in response.json()["error"]["message"]
    no_writes(runtime)


@pytest.mark.parametrize("field", ["template", "dimension_count", "field_mapping", "score_rule"])
def test_clients_cannot_inject_templates_or_guessed_mappings(client, runtime, book, pack, field):
    runtime.service.parser = forbidden_parser
    response = upload(client, book, pack, **{field: "untrusted"})
    assert response.status_code == 422
    no_writes(runtime)


def test_openapi_file_is_only_required_upload_field(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/edu/imports/preview"]["post"]
    schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert schema["required"] == ["file"]
    assert not schema["additionalProperties"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert set(schema["properties"]) == {
        "file",
        "data_scope_id",
        "title",
        "course",
        "assessment_date",
        "revision_of_batch_id",
    }
