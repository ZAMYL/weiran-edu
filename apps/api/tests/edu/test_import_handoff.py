"""Frontend contract is backed by full-file facts and authorized persisted references."""

from datetime import timedelta

import pytest

from .test_direct_upload import upload


def confirm(client, view, **fields):
    info = view["import"]
    return client.post(
        f"/api/v1/edu/imports/{info['import_id']}/commit",
        json={"preview_version": info["preview_version"], **fields},
    )


def open_diagnostics(client, result):
    # No batch-list fetch, manual selection or locally invented snapshot is involved.
    batch_id, snapshot_id = result["batch_id"], result["snapshot_id"]
    batch = client.get(f"/api/v1/edu/batches/{batch_id}")
    assert batch.status_code == 200
    assert batch.json()["snapshot_id"] == snapshot_id
    statistics = client.get(
        f"/api/v1/edu/batches/{batch_id}/statistics", params={"snapshot_id": snapshot_id}
    )
    assert statistics.status_code == 200
    discoveries = client.post(
        f"/api/v1/edu/batches/{batch_id}/discoveries/query",
        json={"snapshot_id": snapshot_id, "limit": 1},
    )
    assert discoveries.status_code == 200
    assert discoveries.json()["scope"]["batch_id"] == batch_id
    assert discoveries.json()["scope"]["snapshot_id"] == snapshot_id
    return batch.json(), statistics.json()


def test_recognized_items_counts_and_quality_are_full_file_facts(client, book, pack):
    for row in book["Sheet2"]:
        if row[0].row not in (1, 2, 8):
            book["Sheet2"].cell(row[0].row, 4).value = None
    view = upload(client, book, pack).json()
    path = f"/api/v1/edu/imports/{view['import']['import_id']}"
    limited = client.get(path + "?row_limit=1&student_limit=1&issue_limit=1").json()
    assert len(limited["rows"]) == 1 and len(limited["issues"]) == 1
    assert limited["recognized_items"] == view["recognized_items"]
    assert limited["summary"] == view["summary"]
    assert limited["summary"]["detected_student_count"] == 2
    assert limited["summary"]["parsed_evaluation_count"] == 10
    assert limited["summary"]["warning_count"] == 10
    items = limited["recognized_items"]
    assert len(items) == 5 and all(item["score_row_count"] == 2 for item in items)
    assert {item["kind"] for item in items} == {"competency"}
    assert all(item["scale"] == {"minimum": 1, "maximum": 5, "step": 0.01} for item in items)
    assert items[0]["display_name"] == "算法与数据结构"
    quality = client.get(path + "/quality-report").json()
    assert quality["status"] == "warning" and quality["validation_passed"]
    assert quality["requires_warning_acknowledgement"]
    group = quality["issue_groups"][0]
    assert (group["code"], group["severity"], group["occurrence_count"]) == (
        "COMMENT_EMPTY",
        "warning",
        10,
    )
    assert group["affected_student_count"] == 2
    assert group["affected_row_count"] == 10
    assert limited["issues"][0]["sheet_name"] == "Sheet2"
    assert limited["issues"][0]["cell"] == "D3"
    assert limited["issues"][0]["row_offset"] == 0


def test_unobserved_dimension_is_not_invented_as_recognized_item(client, book, pack):
    for row in (3, 9):
        for column in (2, 3, 4):
            book["Sheet2"].cell(row, column).value = None
    response = upload(client, book, pack)
    assert response.status_code == 201
    view = response.json()
    assert view["import"]["status"] == "invalid"
    assert len(view["recognized_items"]) == 4
    assert "algorithm_data_structure" not in {i["item_id"] for i in view["recognized_items"]}
    assert view["summary"]["parsed_evaluation_count"] == 8
    assert confirm(client, view).status_code == 409


def test_blocking_errors_and_confirmable_warnings_remain_distinct(client, book, pack):
    book["Sheet2"]["C3"] = None
    book["Sheet2"]["D4"] = None
    view = upload(client, book, pack).json()
    quality = client.get(f"/api/v1/edu/imports/{view['import']['import_id']}/quality-report").json()
    assert quality["status"] == "blocked" and not quality["validation_passed"]
    assert quality["requires_warning_acknowledgement"]
    groups = {i["code"]: i for i in quality["issue_groups"]}
    assert groups["SCORE_REQUIRED"]["severity"] == "error"
    assert groups["SCORE_REQUIRED"]["occurrence_count"] == 1
    assert groups["SCORE_REQUIRED"]["examples"][0]["cell"] == "C3"
    assert groups["COMMENT_EMPTY"]["severity"] == "warning"
    rejected = confirm(client, view, acknowledge_warnings=True)
    assert rejected.status_code == 409
    assert "batch_id" not in rejected.json() and "snapshot_id" not in rejected.json()


def test_patch_keeps_recognition_and_commit_references_open_diagnostics(client, book, pack):
    view = upload(client, book, pack).json()
    changed = client.patch(
        f"/api/v1/edu/imports/{view['import']['import_id']}",
        json={"preview_version": view["import"]["preview_version"], "title": "合成确认名称"},
    ).json()
    assert changed["recognized_items"] == view["recognized_items"]
    assert changed["summary"] == view["summary"]
    assert confirm(client, view).status_code == 409
    created = confirm(client, changed)
    assert created.status_code == 201
    batch, _ = open_diagnostics(client, created.json())
    assert batch["title"] == "合成确认名称"
    assert batch["student_count"] == 2 and batch["evaluation_count"] == 10
    cleared = client.get(f"/api/v1/edu/imports/{view['import']['import_id']}").json()
    assert not cleared["preview_available"] and cleared["recognized_items"] is None


def test_created_duplicate_and_replayed_return_same_persisted_references(client, book, pack):
    first = upload(client, book, pack).json()
    created = confirm(client, first)
    assert created.status_code == 201 and created.json()["result"] == "created"
    second = upload(client, book, pack).json()
    duplicate = confirm(client, second)
    assert duplicate.status_code == 200 and duplicate.json()["result"] == "duplicate"
    replayed = confirm(client, second)
    assert replayed.status_code == 200 and replayed.json()["result"] == "replayed"
    for response in (created, duplicate, replayed):
        assert response.json()["batch_id"] == created.json()["batch_id"]
        assert response.json()["snapshot_id"] == created.json()["snapshot_id"]
        open_diagnostics(client, response.json())


def test_authorized_shared_duplicate_can_open_without_transferring_ownership(
    client, runtime, book, pack
):
    original = confirm(client, upload(client, book, pack).json()).json()
    runtime.admin.grant_read(runtime.other, original["batch_id"])
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    shared = confirm(client, upload(client, book, pack).json())
    assert shared.status_code == 200 and shared.json()["result"] == "duplicate"
    assert shared.json()["snapshot_id"] == original["snapshot_id"]
    open_diagnostics(client, shared.json())
    with runtime.db.transaction() as connection:
        row = connection.execute(
            "SELECT owner_teacher_id FROM batches WHERE batch_id=?", (original["batch_id"],)
        ).fetchone()
        assert row["owner_teacher_id"] == runtime.teacher
        assert connection.execute("SELECT count(*) FROM batches").fetchone()[0] == 1


def test_duplicate_historical_batch_does_not_silently_open_new_revision(client, book, pack):
    first = upload(client, book, pack).json()
    original = confirm(client, first).json()
    book["Sheet2"]["C3"] = 4
    revision = upload(client, book, pack, revision_of_batch_id=original["batch_id"]).json()
    revised = confirm(client, revision)
    assert revised.status_code == 201
    assert revised.json()["snapshot_id"] != original["snapshot_id"]
    book["Sheet2"]["C3"] = 2.75
    duplicate = confirm(client, upload(client, book, pack).json())
    assert duplicate.status_code == 200 and duplicate.json()["result"] == "duplicate"
    assert duplicate.json()["batch_id"] == original["batch_id"]
    assert duplicate.json()["snapshot_id"] == original["snapshot_id"]
    batch, _ = open_diagnostics(client, duplicate.json())
    assert not batch["is_current"]


@pytest.mark.parametrize("revocation", ["token", "scope"])
def test_commit_reference_never_bypasses_later_revocation(client, runtime, book, pack, revocation):
    view = upload(client, book, pack).json()
    created = confirm(client, view).json()
    if revocation == "token":
        runtime.admin.revoke_tokens(runtime.teacher)
    else:
        runtime.admin.remove_member(runtime.teacher, runtime.scope)
    expected = 401 if revocation == "token" else 404
    rejected = client.get(
        f"/api/v1/edu/batches/{created['batch_id']}/statistics",
        params={"snapshot_id": created["snapshot_id"]},
    )
    assert rejected.status_code == expected
    replay = confirm(client, view)
    assert replay.status_code == expected
    assert "snapshot_id" not in replay.json()


def test_reference_with_wrong_snapshot_is_rejected(client, book, pack):
    created = confirm(client, upload(client, book, pack).json()).json()
    response = client.get(
        f"/api/v1/edu/batches/{created['batch_id']}/statistics",
        params={"snapshot_id": "wrong-snapshot"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SNAPSHOT_MISMATCH"


def test_expired_preview_returns_error_not_stale_recognition(client, runtime, book, pack):
    view = upload(client, book, pack).json()
    runtime.clock[0] += timedelta(hours=25)
    response = client.get(f"/api/v1/edu/imports/{view['import']['import_id']}")
    assert response.status_code == 410 and "recognized_items" not in response.json()


def test_new_contract_fields_are_in_real_openapi(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "recognized_items" in schemas["ImportView"]["required"]
    assert "snapshot_id" in schemas["CommitResult"]["required"]
    assert schemas["RecognizedImportItem"]["properties"]["kind"]["const"] == "competency"
