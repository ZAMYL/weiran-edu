"""Authorization, snapshot, strict input and resource regressions for insights."""

import pytest

from .test_insights_api import (
    CONDITIONS,
    A,
    dimension_card,
    group_card,
    query,
    records,
)
from .test_insights_api import (
    formal_batch as formal_batch,
)


@pytest.fixture
def context(client, formal_batch):
    base, batch, _ = formal_batch()
    body = query(client, base, batch, conditions=CONDITIONS).json()
    return base, batch, group_card(body), dimension_card(body)


def test_current_teacher_grants_and_revocation(client, context, runtime):
    base, batch, card, _ = context
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    for snapshot in (batch["snapshot_id"], "incorrect-snapshot"):
        response = query(client, base, batch, snapshot_id=snapshot)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert (
        records(client, base, batch, card, "matched_count", conditions=CONDITIONS).status_code
        == 404
    )
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    assert query(client, base, batch).status_code == 200
    assert (
        records(client, base, batch, card, "matched_count", conditions=CONDITIONS).status_code
        == 200
    )
    runtime.admin.revoke_read(runtime.other, batch["batch_id"])
    assert query(client, base, batch).status_code == 404


@pytest.mark.parametrize("revocation", ["token", "teacher", "membership"])
def test_revoked_owner_cannot_reuse_candidate(client, context, runtime, revocation):
    base, batch, card, _ = context
    if revocation == "token":
        runtime.admin.revoke_tokens(runtime.teacher)
    elif revocation == "teacher":
        runtime.admin.set_active(runtime.teacher, False)
    else:
        runtime.admin.remove_member(runtime.teacher, runtime.scope)
    expected = 404 if revocation == "membership" else 401
    assert query(client, base, batch).status_code == expected
    assert (
        records(client, base, batch, card, "matched_count", conditions=CONDITIONS).status_code
        == expected
    )


@pytest.mark.parametrize("drill", [False, True])
def test_transaction_rechecks_credential_after_initial_auth(
    client,
    context,
    runtime,
    monkeypatch,
    drill,
):
    import app.routers.edu as routes

    base, batch, card, _ = context
    original = routes.read_query

    async def revoke_after_decode(request, model):
        body = await original(request, model)
        runtime.admin.revoke_tokens(runtime.teacher)
        return body

    monkeypatch.setattr(routes, "read_query", revoke_after_decode)
    response = (
        records(client, base, batch, card, "matched_count", conditions=CONDITIONS)
        if drill
        else query(client, base, batch)
    )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("drill", [False, True])
def test_authentication_precedes_request_body(client, context, monkeypatch, drill):
    import app.routers.edu as routes

    base, _, card, _ = context

    async def forbidden_read(*_):
        raise AssertionError("Unauthenticated body was decoded")

    monkeypatch.setattr(routes, "read_query", forbidden_read)
    client.headers.pop("Authorization")
    path = (
        base + "/insights/" + card["candidate_id"] + "/records/query"
        if drill
        else base + "/insights/query"
    )
    result = client.post(path, content=b"not json", headers={"X-Teacher-ID": "forged"})
    assert result.status_code == 401


def test_revision_snapshot_candidates_and_evidence_cannot_cross_batches(client, formal_batch):
    old_base, old, _ = formal_batch()
    old_card = dimension_card(query(client, old_base, old).json())
    first = records(client, old_base, old, old_card, "mean").json()["items"][0]
    new_base, new, _ = formal_batch(revision=old["batch_id"], change=True)
    assert new["snapshot_id"] != old["snapshot_id"]
    assert query(client, new_base, new, snapshot_id=old["snapshot_id"]).status_code == 409
    assert records(client, new_base, new, old_card, "mean").status_code == 404
    assert (
        records(client, old_base, old, old_card, "mean", snapshot_id=new["snapshot_id"]).status_code
        == 409
    )
    assert (
        client.get(new_base + "/evidence/" + first["scores"][0]["evidence_id"]).status_code == 404
    )
    assert client.get(old_base + "/evidence/forged-evidence-id").status_code == 404
    assert records(client, old_base, old, old_card, "mean").status_code == 200
    assert query(client, new_base, new).status_code == 200


def test_unknown_metric_candidate_changed_plan_and_injected_evidence(client, context):
    base, batch, card, stats = context
    fake = {**card, "candidate_id": "candidate-" + "f" * 24}
    assert (
        records(client, base, batch, fake, "matched_count", conditions=CONDITIONS).status_code
        == 404
    )
    assert records(client, base, batch, card, "matched_count").status_code == 404
    assert records(client, base, batch, stats, "mean", include_dimensions=False).status_code == 404
    assert records(client, base, batch, stats, "unknown_metric").status_code == 404
    assert (
        records(client, base, batch, stats, "mean", evidence_id="forged-evidence").status_code
        == 422
    )
    assert (
        records(client, base, batch, stats, "mean", student_ids=["forged-student"]).status_code
        == 422
    )
    assert query(client, base, batch, snapshot_id="wrong").status_code == 409


@pytest.mark.parametrize(
    "patch",
    [
        {"snapshot_id": None},
        {"snapshot_id": ""},
        {"include_dimensions": "true"},
        {"conditions": CONDITIONS * 2},
        {"conditions": [CONDITIONS[0]] * 2},
        {"conditions": [{"dimension_code": A, "operator": ">=", "value": 4}]},
        {"conditions": [{"dimension_code": A, "operator": "gte", "value": "2.75"}]},
        {"conditions": [{"dimension_code": A, "operator": "gte", "value": 2.751}]},
        {"conditions": [{"dimension_code": A, "operator": "gte", "value": True}]},
        {"offset": -1},
        {"offset": True},
        {"limit": 0},
        {"limit": 101},
        {"limit": "20"},
        {"sql": "SELECT private_secret"},
        {"student_ids": []},
        {"teacher_id": "forged"},
    ],
)
def test_closed_query_contract(client, context, patch):
    base, batch, _, _ = context
    response = query(client, base, batch, **patch)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "private_secret" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "raw",
    [
        '{"snapshot_id":"x","snapshot_id":"y"}',
        '{"snapshot_id":"x","conditions":[{"dimension_code":"algorithm_data_structure",'
        '"operator":"eq","value":2.750000000000000000000000000001}]}',
        '{"snapshot_id":"x","conditions":[{"dimension_code":"algorithm_data_structure",'
        '"operator":"eq","value":NaN}]}',
        '{"snapshot_id":"x","conditions":[{"dimension_code":"algorithm_data_structure",'
        '"operator":"eq","value":1e999999999999999999999999999999}]}',
        "[]",
        "{",
        '{"conditions":' + "[" * 1100,
    ],
)
def test_malformed_json_never_returns_normal_cards(client, context, raw):
    base, _, _, _ = context
    response = client.post(
        base + "/insights/query", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert "2.7500000" not in response.text


def test_stream_size_media_type_missing_snapshot_and_numeric_lexeme(client, context):
    base, batch, _, _ = context
    assert client.post(base + "/insights/query", json={}).status_code == 422
    assert (
        client.post(
            base + "/insights/query", content="{}", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert (
        client.post(
            base + "/insights/query",
            content=b" " * 65537,
            headers={"Content-Type": "application/json", "Content-Length": "1"},
        ).status_code
        == 413
    )
    raw = (
        '{"snapshot_id":"' + batch["snapshot_id"] + '","include_dimensions":false,'
        '"conditions":[{"dimension_code":"algorithm_data_structure","operator":"eq","value":275e-2}]}'
    )
    result = client.post(
        base + "/insights/query", content=raw, headers={"Content-Type": "application/json"}
    ).json()
    assert result["items"][0]["metrics"][0]["value"]["value"] == 1


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("MAX_RECORDS", 0, "INSIGHT_LIMIT_EXCEEDED"),
        ("MAX_WORK", 0, "INSIGHT_LIMIT_EXCEEDED"),
        ("MAX_SECONDS", 0, "INSIGHT_TIMEOUT"),
        ("MAX_RESPONSE_BYTES", 1, "INSIGHT_RESULT_TOO_LARGE"),
    ],
)
def test_resource_budgets_fail_explicitly(client, context, monkeypatch, name, value, expected):
    import app.edu.insights as insights

    base, batch, _, _ = context
    monkeypatch.setattr(insights, name, value)
    result = query(client, base, batch)
    assert result.status_code == (413 if name == "MAX_RESPONSE_BYTES" else 503)
    assert result.json()["error"]["code"] == expected


def test_busy_slot_is_bounded_and_releases_after_use(client, context):
    from app.edu.insights import _COMPUTE_SLOTS

    base, batch, _, _ = context
    assert _COMPUTE_SLOTS.acquire(blocking=False)
    assert _COMPUTE_SLOTS.acquire(blocking=False)
    try:
        assert query(client, base, batch).json()["error"]["code"] == "INSIGHTS_BUSY"
    finally:
        _COMPUTE_SLOTS.release()
        _COMPUTE_SLOTS.release()
    assert query(client, base, batch).status_code == 200


def test_runtime_openapi_documents_actual_new_contract(client):
    schema = client.get("/openapi.json").json()
    prefix = "/api/v1/edu/batches/{batch_id}/insights"
    for suffix in ("/query", "/{candidate_id}/records/query"):
        method = schema["paths"][prefix + suffix]["post"]
        assert method["security"]
        for status in ("401", "404", "409", "413", "415", "422", "503"):
            error = method["responses"][status]["content"]["application/json"]["schema"]
            assert error["$ref"].endswith("/ErrorResponse")
        request = method["requestBody"]["content"]["application/json"]["schema"]
        assert "snapshot_id" in request["required"]
        assert request["additionalProperties"] is False
        assert request["properties"]["conditions"]["maxItems"] == 2
        assert (
            request["properties"]["conditions"]["items"]["properties"]["value"]["type"] == "number"
        )
        assert method["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]


def test_unconfirmed_preview_is_not_a_formal_batch(client, runtime, book, pack):
    from .test_http import commit, upload

    response = upload(client, runtime, book, pack)
    assert response.status_code == 201
    view = response.json()
    import_id = view["import"]["import_id"]
    attempted = client.post(
        "/api/v1/edu/batches/" + import_id + "/insights/query",
        json={"snapshot_id": view["import"]["preview_version"]},
    )
    assert attempted.status_code == 404
    confirmed = commit(client, view)
    assert confirmed.status_code == 201
    base = "/api/v1/edu/batches/" + confirmed.json()["batch_id"]
    batch = client.get(base).json()
    assert query(client, base, batch).status_code == 200


def test_permissions_load_discovery_and_evidence_share_read_transaction(
    client,
    context,
    monkeypatch,
):
    import app.edu.insights as insights

    base, batch, _, stats = context
    original_load = insights.InsightReader._load
    original_discover = insights.discover_candidates
    connections = []

    def checked_load(connection, batch):
        assert connection.in_transaction
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        connections.append(connection)
        return original_load(connection, batch)

    def checked_discover(*args, **kwargs):
        assert connections[-1].in_transaction
        return original_discover(*args, **kwargs)

    monkeypatch.setattr(insights.InsightReader, "_load", staticmethod(checked_load))
    monkeypatch.setattr(insights, "discover_candidates", checked_discover)
    assert records(client, base, batch, stats, "mean").status_code == 200
    assert len(connections) == 1  # Data is not reread in a second connection.


def test_damaged_persisted_facts_fail_both_insight_paths(client, context, runtime):
    base, batch, _, stats = context
    # Disposable test database only: simulate external corruption of immutable facts.
    with runtime.db.transaction(write=True) as connection:
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='evaluations'"
        ).fetchall()
        for row in triggers:
            connection.execute('DROP TRIGGER "' + row[0].replace('"', '""') + '"')
        connection.execute(
            "DELETE FROM evaluations WHERE evidence_id=(SELECT evidence_id FROM evaluations "
            "WHERE batch_id=? LIMIT 1)",
            (batch["batch_id"],),
        )
    for response in (query(client, base, batch), records(client, base, batch, stats, "mean")):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"


@pytest.mark.parametrize(
    "patch",
    [
        {"metric_key": None},
        {"metric_key": ""},
        {"metric_key": "x" * 81},
        {"limit": True},
        {"offset": -1},
    ],
)
def test_records_body_is_strict(client, context, patch):
    base, batch, _, stats = context
    response = records(client, base, batch, stats, **{"metric_key": "mean", **patch})
    assert response.status_code == 422
