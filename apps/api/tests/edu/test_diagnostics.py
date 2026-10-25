"""Synthetic-only diagnostics, readiness and authorization regressions."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import APIError
from app.edu.diagnostics import DiagnosticReader
from app.main import create_app
from app.storage.database import Database

from .conftest import commit
from .test_snapshot_integrity import damage_second_student


@pytest.fixture
def batch_context(client, runtime, stage):
    committed = commit(runtime, stage())
    batch = runtime.service.get_batch(runtime.p, committed.batch_id)
    return f"/api/v1/edu/batches/{batch.batch_id}", batch


def diagnose(client, base, batch, **patch):
    return client.post(
        base + "/diagnostics/integrity",
        json={
            "snapshot_id": batch.snapshot_id,
            **patch,
        },
    )


def test_ready_is_public_and_private_metadata_is_not_returned(client, runtime):
    response = client.get("/api/v1/health/ready", headers={"Authorization": ""})
    assert response.status_code == 200 and response.json() == {"status": "ready"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert str(runtime.db.path) not in response.text


@pytest.mark.parametrize(
    "state",
    [
        "missing_migration",
        "checksum",
        "unknown_version",
        "missing_index",
        "missing_trigger",
        "missing_table",
    ],
)
def test_not_ready_does_not_change_or_repair_database(client, runtime, state, caplog):
    with runtime.db.transaction(write=True) as connection:
        if state == "missing_migration":
            connection.execute(
                "DELETE FROM schema_migrations WHERE version='003_immutable_students.sql'"
            )
        elif state == "checksum":
            connection.execute("UPDATE schema_migrations SET checksum='tampered'")
        elif state == "unknown_version":
            connection.execute(
                "INSERT INTO schema_migrations VALUES ('999_unknown.sql','unexpected','2026-10-25')"
            )
        elif state == "missing_index":
            connection.execute("DROP INDEX evaluations_filter")
        elif state == "missing_trigger":
            connection.execute("DROP TRIGGER immutable_evaluations_update")
        else:
            connection.execute("DROP TABLE import_payloads")
    before = runtime.db.path.read_bytes()
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_NOT_READY"
    assert response.headers["cache-control"] == "no-store"
    assert runtime.db.path.read_bytes() == before
    assert all(
        secret not in response.text
        for secret in ("MIGRATION", "SCHEMA_OBJECT", "tampered", str(runtime.db.path))
    )
    assert "Readiness failed: code=" in caplog.text
    assert str(runtime.db.path) not in caplog.text
    assert client.get("/api/v1/health").status_code == 200


@pytest.mark.parametrize("configured", [False, True])
def test_no_database_configuration_or_file_is_never_implicitly_initialized(tmp_path, configured):
    missing = tmp_path / "missing-parent" / "missing.sqlite3"
    application = create_app(
        Settings(environment="test", database_path=missing if configured else None)
    )
    with TestClient(application) as client:
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_NOT_READY"
    assert not missing.parent.exists()


def test_owner_capabilities_distinguish_permissions_from_unregistered_features(
    client, batch_context
):
    base, batch = batch_context
    response = client.get(base + "/capabilities", params={"snapshot_id": batch.snapshot_id})
    assert response.status_code == 200
    result = response.json()
    assert result["role"] == "owner"
    actions = {item["action"]: item for item in result["operations"]}
    assert actions["read_batch"]["allowed"] is True
    assert actions["import_data"]["allowed"] is True
    assert actions["diagnose_integrity"]["allowed"] is True
    for name in ("review_text", "save_analysis", "edit_analysis", "export_analysis"):
        # In the PR #25 combination these actual routes are present; otherwise false.
        if not actions[name]["available"]:
            assert actions[name]["allowed"] is False
            assert actions[name]["reason"] == "feature_unavailable"
        else:
            assert actions[name]["allowed"] is True


def test_reader_capabilities_and_integrity_restrictions_remain_current(
    client, runtime, batch_context
):
    base, batch = batch_context
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    assert client.get(base + "/capabilities").status_code == 404
    assert diagnose(client, base, batch, snapshot_id="wrong").status_code == 404
    runtime.admin.grant_read(runtime.other, batch.batch_id)
    runtime.admin.set_member(runtime.other, runtime.scope, can_import=False)
    response = client.get(base + "/capabilities").json()
    assert response["role"] == "reader"
    actions = {item["action"]: item for item in response["operations"]}
    assert actions["read_batch"]["allowed"] is True
    assert actions["import_data"]["reason"] == "import_permission_required"
    assert actions["diagnose_integrity"]["reason"] == "owner_required"
    assert actions["review_text"]["allowed"] is False
    assert diagnose(client, base, batch).status_code == 403
    assert diagnose(client, base, batch, snapshot_id="wrong").status_code == 403
    runtime.admin.remove_member(runtime.other, runtime.scope)
    assert client.get(base + "/capabilities").status_code == 404
    assert diagnose(client, base, batch).status_code == 404


def test_integrity_report_checks_entire_batch_without_writes(client, runtime, batch_context):
    base, batch = batch_context
    before = runtime.db.path.read_bytes()
    response = diagnose(client, base, batch)
    assert response.status_code == 200
    report = response.json()
    assert report["passed"] is True and report["repaired"] is False
    assert report["scope"] == "stored_fact_metadata"
    assert report["actual_student_count"] == report["expected_student_count"] == 2
    assert report["actual_evaluation_count"] == report["expected_evaluation_count"] == 10
    assert len(report["checks"]) == 10
    assert all(item["passed"] and item["violation_count"] == 0 for item in report["checks"])
    assert runtime.db.path.read_bytes() == before
    assert all(
        secret not in response.text
        for secret in ("合成姓名", "000000000001", "合成评语", "student_pk")
    )


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("missing", "EVALUATION_COUNT"),
        ("source_hash", "EVIDENCE_SOURCE_HASH"),
        ("source_cell", "EVIDENCE_COORDINATES"),
        ("rubric_range", "RUBRIC_SOURCE"),
    ],
)
def test_owner_can_diagnose_damaged_snapshot_while_fact_reads_stay_closed(
    client, runtime, batch_context, kind, code
):
    base, batch = batch_context
    damage_second_student(runtime, batch.batch_id, kind)
    before = runtime.db.path.read_bytes()
    response = diagnose(client, base, batch)
    assert response.status_code == 200
    report = response.json()
    assert report["passed"] is False and report["repaired"] is False
    check = next(item for item in report["checks"] if item["code"] == code)
    assert check["passed"] is False and check["violation_count"] > 0
    assert len(report["checks"]) == 10
    assert client.get(base + "/statistics").status_code == 503
    assert client.get(base + "/students?limit=1").status_code == 503
    assert client.get(base + "/capabilities").json()["operations"][0]["allowed"] is True
    assert runtime.db.path.read_bytes() == before


def test_multiple_failures_are_aggregated_and_database_text_is_not_echoed(
    client, runtime, batch_context
):
    base, batch = batch_context
    with runtime.db.transaction(write=True) as connection:
        connection.execute("DROP TRIGGER immutable_evaluations_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE evaluations SET score_units=99,score_cell='private-marker',file_sha256=? "
            "WHERE rowid=1",
            ("0" * 64,),
        )
    report = diagnose(client, base, batch)
    failed = {item["code"] for item in report.json()["checks"] if not item["passed"]}
    assert {"SCORE_RANGE", "EVIDENCE_COORDINATES", "EVIDENCE_SOURCE_HASH"} <= failed
    assert "private-marker" not in report.text


def test_authorization_snapshot_and_revocation_precede_diagnostic_work(
    client, runtime, batch_context, monkeypatch
):
    import app.edu.diagnostics as diagnostics

    base, batch = batch_context

    def forbidden(*_):
        raise AssertionError("Unauthorized inspection")

    monkeypatch.setattr(diagnostics, "inspect_snapshot", forbidden)
    assert diagnose(client, base, batch, snapshot_id="wrong").status_code == 409
    runtime.admin.revoke_tokens(runtime.teacher)
    assert diagnose(client, base, batch).status_code == 401
    assert client.get(base + "/capabilities").status_code == 401
    with pytest.raises(APIError) as error:
        DiagnosticReader(runtime.db).capabilities(runtime.p, batch.batch_id)
    assert error.value.status_code == 401


def test_transaction_rechecks_token_revoked_during_json_decode(
    client, runtime, batch_context, monkeypatch
):
    import app.routers.edu as routes

    base, batch = batch_context
    original = routes.read_query

    async def revoke(request, model):
        body = await original(request, model)
        runtime.admin.revoke_tokens(runtime.teacher)
        return body

    monkeypatch.setattr(routes, "read_query", revoke)
    assert diagnose(client, base, batch).status_code == 401


@pytest.mark.parametrize(
    "patch", [{"snapshot_id": None}, {"snapshot_id": ""}, {"snapshot_id": True}, {"repair": True}]
)
def test_diagnostic_body_is_closed_and_does_not_accept_repair(client, batch_context, patch):
    base, batch = batch_context
    assert diagnose(client, base, batch, **patch).status_code == 422


def test_no_private_source_reads_and_no_sqlite_writes(client, runtime, batch_context, monkeypatch):
    base, batch = batch_context
    original = Database._connect
    denied = {
        "raw_xlsx",
        "student_number",
        "student_name",
        "identity_cell_raw",
        "comment_raw",
        "comment_display",
        "text_cells_json",
    }

    def connect(self):
        connection = original(self)

        def authorize(action, table, column, database, trigger):
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ and column in denied:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(Database, "_connect", connect)
    assert client.get("/api/v1/health/ready").status_code == 200
    assert client.get(base + "/capabilities").status_code == 200
    assert diagnose(client, base, batch).status_code == 200


def test_owner_authorization_and_inspection_share_readonly_transaction(
    client, batch_context, monkeypatch
):
    import app.edu.diagnostics as diagnostics

    base, batch = batch_context
    original_authorized = DiagnosticReader._authorized
    original_inspect = diagnostics.inspect_snapshot
    connections = []

    def authorized(self, connection, *args, **kwargs):
        result = original_authorized(self, connection, *args, **kwargs)
        connections.append(connection)
        return result

    def inspect(connection, row):
        assert connection is connections[-1] and connection.in_transaction
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        return original_inspect(connection, row)

    monkeypatch.setattr(DiagnosticReader, "_authorized", authorized)
    monkeypatch.setattr(diagnostics, "inspect_snapshot", inspect)
    assert diagnose(client, base, batch).status_code == 200
    assert len(connections) == 1


def test_new_openapi_contracts_are_visible(client):
    paths = client.get("/openapi.json").json()["paths"]
    ready = paths["/api/v1/health/ready"]["get"]
    assert "security" not in ready
    assert "503" in ready["responses"]
    base = "/api/v1/edu/batches/{batch_id}"
    assert paths[base + "/capabilities"]["get"]["security"]
    integrity = paths[base + "/diagnostics/integrity"]["post"]
    assert integrity["security"]
    assert "403" in integrity["responses"]
    schema = integrity["requestBody"]["content"]["application/json"]["schema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["snapshot_id"]
