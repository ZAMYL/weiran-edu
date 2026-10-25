"""Private request metadata, fail-soft writes, explicit storage and bounded retention."""

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.errors import APIError
from app.storage.request_traces import RequestTraceStore

TRACE = "/api/v1/edu/diagnostics/requests/"
PRIVATE = "synthetic-private-request-marker"


@pytest.fixture
def traces(client, tmp_path):
    store = RequestTraceStore(tmp_path / "traces.sqlite3")
    store.initialize()
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"request_trace_database_path": store.database.path}
    )
    return store


def rows(store):
    with store.database.transaction() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM request_traces")]


def record(store, *, teacher="teacher-a", now=None):
    request_id = "req_" + uuid4().hex
    assert store.record(
        request_id=request_id,
        teacher_id=teacher,
        route_template="/api/v1/edu/me",
        method="GET",
        status_code=200,
        error_code=None,
        stage="completed",
        duration_ms=3,
        now=now,
    )
    return request_id


def test_authenticated_success_record_is_private_no_store_and_nonrecursive(client, runtime, traces):
    before = runtime.db.path.read_bytes()
    response = client.get("/api/v1/edu/me", headers={"X-Request-ID": PRIVATE})
    assert response.status_code == 200 and response.headers["X-Request-Trace"] == "recorded"
    request_id = response.headers["X-Request-ID"]
    trace = client.get(TRACE + request_id)
    assert trace.status_code == 200, trace.text
    body = trace.json()
    assert set(body) == {
        "request_id",
        "route_template",
        "method",
        "status_code",
        "error_code",
        "stage",
        "duration_ms",
        "created_at",
        "expires_at",
    }
    assert body["request_id"] == request_id and body["route_template"] == "/api/v1/edu/me"
    assert (
        body["status_code"] == 200 and body["stage"] == "completed" and body["error_code"] is None
    )
    assert body["duration_ms"] >= 0
    assert trace.headers["Cache-Control"] == "no-store"
    assert "X-Request-Trace" not in trace.headers
    assert len(rows(traces)) == 1
    assert runtime.db.path.read_bytes() == before
    assert PRIVATE not in str(rows(traces)) + trace.text
    assert runtime.teacher not in trace.text and runtime.token not in trace.text


@pytest.mark.parametrize(
    "request_kind,status,code,stage",
    [
        ("missing", 404, "RESOURCE_NOT_FOUND", "authorization_or_resource"),
        ("invalid", 422, "VALIDATION_ERROR", "validation"),
        ("media", 415, "UNSUPPORTED_MEDIA_TYPE", "validation"),
        ("failure", 500, "INTERNAL_ERROR", "server"),
    ],
)
def test_error_metadata_uses_template_and_never_stores_input(
    client, runtime, traces, monkeypatch, request_kind, status, code, stage
):
    if request_kind == "missing":
        response = client.get("/api/v1/edu/batches/" + PRIVATE + "?private=" + PRIVATE)
    elif request_kind == "invalid":
        response = client.get("/api/v1/edu/batches?limit=" + PRIVATE)
    elif request_kind == "media":
        response = client.post("/api/v1/edu/imports/preview", content=PRIVATE)
    else:
        from app.edu.reads import BatchReader

        def broken(*args, **kwargs):
            raise RuntimeError(PRIVATE)

        monkeypatch.setattr(BatchReader, "students", broken)
        response = client.get("/api/v1/edu/batches/" + PRIVATE + "/students")
    assert response.status_code == status, response.text
    assert response.headers["X-Request-Trace"] == "recorded"
    trace = client.get(TRACE + response.headers["X-Request-ID"])
    assert trace.status_code == 200, trace.text
    assert trace.json()["error_code"] == code and trace.json()["stage"] == stage
    assert trace.json()["status_code"] == status
    assert PRIVATE not in str(rows(traces)) + trace.text
    assert runtime.token not in str(rows(traces))


def test_other_teacher_unknown_expired_and_invalid_ids_have_same_safe_404(client, runtime, traces):
    response = client.get("/api/v1/edu/me")
    request_id = response.headers["X-Request-ID"]
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    for value in (request_id, "req_" + "0" * 32, PRIVATE):
        result = client.get(TRACE + value)
        assert result.status_code == 404
        assert result.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert PRIVATE not in result.text
    client.headers["Authorization"] = "Bearer " + runtime.token
    with traces.database.transaction(write=True) as connection:
        connection.execute("UPDATE request_traces SET expires_at='2000-01-01T00:00:00Z'")
    assert client.get(TRACE + request_id).status_code == 404
    runtime.admin.revoke_tokens(runtime.teacher)
    assert client.get(TRACE + request_id).status_code == 401


def test_unauthenticated_health_unknown_route_and_trace_lookup_never_record(client, traces):
    client.get("/api/v1/health")
    client.get("/" + PRIVATE)
    client.get(TRACE + "req_" + "0" * 32)
    client.headers.pop("Authorization")
    assert client.get("/api/v1/edu/me").status_code == 401
    assert client.get(TRACE + "req_" + "0" * 32).status_code == 401
    assert not rows(traces)


@pytest.mark.parametrize("storage", ["disabled", "missing", "corrupt", "locked", "business"])
def test_telemetry_failure_preserves_business_success_without_implicit_creation(
    client, runtime, traces, tmp_path, storage
):
    lock = None
    path = traces.database.path
    if storage == "disabled":
        path = None
    elif storage == "missing":
        path = tmp_path / "missing.sqlite3"
    elif storage == "corrupt":
        path.write_bytes(b"synthetic-invalid-database")
    elif storage == "locked":
        lock = sqlite3.connect(path)
        lock.execute("BEGIN EXCLUSIVE")
    elif storage == "business":
        path = runtime.db.path
    before = runtime.db.path.read_bytes()
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"request_trace_database_path": path}
    )
    try:
        response = client.get("/api/v1/edu/me")
        assert response.status_code == 200
        assert response.headers["X-Request-Trace"] == (
            "disabled" if storage == "disabled" else "unavailable"
        )
        lookup = client.get(TRACE + response.headers["X-Request-ID"])
        assert lookup.status_code == 503
        assert lookup.json()["error"]["code"] == "REQUEST_TRACE_UNAVAILABLE"
        assert runtime.db.path.read_bytes() == before
        if storage == "missing":
            assert not path.exists()
    finally:
        if lock:
            lock.rollback()
            lock.close()


def test_unexpected_telemetry_exception_cannot_change_success_or_error(client, traces, monkeypatch):
    import app.core.middleware as middleware

    async def broken(*args, **kwargs):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(middleware, "capture_trace", broken)
    assert client.get("/api/v1/edu/me").status_code == 200
    assert client.get("/api/v1/edu/batches/missing").status_code == 404


def test_retention_expiry_per_teacher_and_global_caps(tmp_path, monkeypatch):
    import app.storage.request_traces as module

    monkeypatch.setattr(module, "MAX_PER_TEACHER", 2)
    monkeypatch.setattr(module, "MAX_RECORDS", 3)
    store = RequestTraceStore(tmp_path / "retention.sqlite3")
    store.initialize()
    start = datetime.now(UTC) - timedelta(hours=1)
    ids = [record(store, now=start + timedelta(seconds=i)) for i in range(3)]
    assert store.get("teacher-a", ids[0]) is None
    assert len(rows(store)) == 2
    record(store, teacher="teacher-b", now=start + timedelta(seconds=4))
    record(store, teacher="teacher-b", now=start + timedelta(seconds=5))
    assert len(rows(store)) == 3 and store.get("teacher-a", ids[1]) is None
    assert store.get("teacher-a", ids[2], now=start + timedelta(days=8)) is None
    record(store, teacher="teacher-c", now=start + timedelta(days=8))
    assert len(rows(store)) == 1


def test_explicit_cli_is_idempotent_refuses_business_db_and_cleanup_is_safe(
    runtime, tmp_path, capsys
):
    from app.edu.trace_admin import main

    path = tmp_path / "operator.sqlite3"
    assert main(["--database", str(path), "init"]) == 0
    assert main(["--database", str(path), "init"]) == 0
    store = RequestTraceStore(path)
    record(store, now=datetime.now(UTC) - timedelta(days=8))
    assert main(["--database", str(path), "cleanup"]) == 0
    assert not rows(store)
    before = runtime.db.path.read_bytes()
    assert main(["--database", str(runtime.db.path), "init"]) == 1
    assert runtime.db.path.read_bytes() == before
    assert "TRACE_ADMIN_FAILED" in capsys.readouterr().out


def test_checksum_corruption_is_unavailable_without_overwrite(traces):
    with traces.database.transaction(write=True) as connection:
        connection.execute("UPDATE request_trace_schema SET checksum='invalid'")
    with pytest.raises(APIError, match="请求诊断记录暂不可用"):
        traces.get("teacher-a", "req_" + "0" * 32)
    with pytest.raises(APIError):
        traces.initialize()


def test_trace_setting_is_private_and_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("WEIRAN_EDU_REQUEST_TRACE_DATABASE", str(tmp_path / "private.sqlite3"))
    settings = Settings.from_env()
    assert settings.request_trace_database_path == tmp_path / "private.sqlite3"
    assert "private.sqlite3" not in repr(settings) + settings.model_dump_json()


def test_committed_result_and_conflict_remain_unchanged_when_telemetry_fails(
    client, runtime, traces, monkeypatch, book, pack
):
    from .test_http import commit, upload
    from .test_reviewed_results import save_result

    preview = upload(client, runtime, book, pack).json()
    batch = commit(client, preview, acknowledge_warnings=True).json()
    base = "/api/v1/edu/batches/" + batch["batch_id"]
    batch = client.get(base).json()
    monkeypatch.setattr(RequestTraceStore, "record", lambda *args, **kwargs: False)
    saved = save_result(client, base, batch, conditions=[])
    assert saved.status_code == 201, saved.text
    assert saved.headers["X-Request-Trace"] == "unavailable"
    assert client.get(base + "/analysis-results").json()["total_count"] == 1
    monkeypatch.undo()
    response = client.post(
        base + "/analysis-results/" + saved.json()["result_id"] + "/export-check",
        json={"snapshot_id": batch["snapshot_id"], "expected_version": 99},
    )
    assert response.status_code == 409
    trace = client.get(TRACE + response.headers["X-Request-ID"]).json()
    assert trace["error_code"] == "ANALYSIS_RESULT_VERSION_MISMATCH"
    assert trace["stage"] == "conflict"
    assert (
        trace["route_template"]
        == "/api/v1/edu/batches/{batch_id}/analysis-results/{result_id}/export-check"
    )


def test_trace_query_rechecks_credentials_after_dependency(client, runtime, traces, monkeypatch):
    import app.routers.edu as routes

    request_id = client.get("/api/v1/edu/me").headers["X-Request-ID"]
    original = routes.authenticate

    def revoked(database, token):
        actor = original(database, token)
        runtime.admin.revoke_tokens(actor.teacher_id)
        return actor

    monkeypatch.setattr(routes, "authenticate", revoked)
    assert client.get(TRACE + request_id).status_code == 401
