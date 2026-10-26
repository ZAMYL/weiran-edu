"""Real FastAPI/auth/SQLite with a clearly test-only engine-free contract harness."""

import sqlite3
from dataclasses import replace
from fractions import Fraction

import pytest
from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.edu.analysis import DIMENSION_MAP
from app.edu.discovery_bridge import DiscoveryInputReader
from app.edu.discovery_models import DiscoveryQuery, DiscoverySpec, DiscoverySummary
from app.edu.query import read_query
from app.routers.edu import Actor, Backend

from .test_insights_api import formal_batch as formal_batch


@pytest.fixture
def bridge_http(client, runtime):
    state = {"mode": "normal", "hook": lambda: None}

    @client.app.post("/api/v1/edu/__test_only/discovery/{batch_id}/{operation}")
    async def harness(
        batch_id: str, operation: str, request: Request, actor: Actor, backend: Backend
    ):
        # Never registered by production create_app. Cards are explicit synthetic test doubles.
        query = await read_query(request, DiscoveryQuery)
        spec = DiscoverySpec(snapshot_id=query.snapshot_id, scope_conditions=query.scope_conditions)

        def execute():
            reader = DiscoveryInputReader(backend.database, clock=backend.clock)
            with reader.snapshot(actor, batch_id, spec) as data:
                state["input"] = data
                cards = tuple(
                    DiscoverySummary(
                        discovery_id=f"{index:024x}",
                        batch_id=batch_id,
                        snapshot_id=spec.snapshot_id,
                        input_key=data.scope.input_key,
                        rule_version="synthetic-test-double.v1",
                        type="dimension_relation",
                        title="合成测试卡",
                        summary="仅测试契约分页",
                        status="interpret_cautiously",
                    )
                    for index in range(3 if data.student_ids else 0)
                )
                if operation == "unknown":
                    return reader.finding(cards, "f" * 24)
                if operation == "sources":
                    refs = tuple(
                        r.reference
                        for r in data.dataset.records
                        if r.reference.student_id in data.student_ids
                    )
                    if state["mode"] == "foreign":
                        refs = (replace(refs[0], batch_id="synthetic-other-batch"),)
                    if state["mode"] == "wrong-dimension":
                        refs = (
                            replace(
                                refs[0],
                                evidence_refs=tuple(
                                    replace(e, dimension=list(DIMENSION_MAP.values())[-1])
                                    for e in refs[0].evidence_refs
                                ),
                            ),
                        )
                    result = reader.resolve_sources(
                        actor,
                        data,
                        refs,
                        (list(DIMENSION_MAP.values())[0],),
                        metric_key="paired_scores",
                        offset=query.offset,
                        limit=query.limit,
                    )
                else:
                    result = reader.page(
                        data.scope,
                        cards,
                        qualified_count=len(cards) + 2 if cards else 0,
                        rule_version="synthetic-test-double.v1",
                        offset=query.offset,
                        limit=query.limit,
                    )
                state["hook"]()
                return result

        return await run_in_threadpool(execute)

    return state


def call(client, batch, operation="list", **patch):
    return client.post(
        f"/api/v1/edu/__test_only/discovery/{batch['batch_id']}/{operation}",
        json={"snapshot_id": batch["snapshot_id"], **patch},
    )


def test_full_snapshot_exact_scores_paging_and_retained_denominator(
    client, formal_batch, bridge_http
):
    _, batch, _ = formal_batch(53)
    first = call(client, batch, limit=2)
    assert first.status_code == 200, first.text
    one = first.json()
    two = call(client, batch, offset=2, limit=2).json()
    assert one["scope"]["batch_record_count"] == one["scope"]["scope_record_count"] == 53
    assert one["qualified_count"] == 5 and one["retained_count"] == 3 and one["truncated"]
    assert one["page"]["has_more"] and not two["page"]["has_more"] and len(two["items"]) == 1
    assert one["scope"]["statistics"] == two["scope"]["statistics"]
    mean = one["scope"]["statistics"]["dimensions"][0]["mean"]["exact"]
    assert Fraction(mean["numerator"], mean["denominator"]) == Fraction(619, 212)
    data = bridge_http["input"]
    assert len(data.dataset.records) == 53
    assert data.dataset.policy.step == Fraction(1, 100)
    assert all(
        s.value.denominator in (1, 2, 4, 5, 10, 20, 25, 50, 100)
        for r in data.dataset.records
        for s in r.scores
    )
    assert any(s.value == Fraction(11, 4) for r in data.dataset.records for s in r.scores)
    assert first.headers["cache-control"] == "no-store" and first.headers[
        "x-request-id"
    ].startswith("req_")
    assert all(
        marker not in first.text
        for marker in ("student_id", "evidence_id", "合成私密姓名", "comment_raw")
    )


def test_empty_scope_and_canonical_condition_key(client, formal_batch, bridge_http):
    _, batch, _ = formal_batch(17)
    conditions = [{"dimension_code": "algorithm_data_structure", "operator": "lt", "value": 1}]
    empty = call(client, batch, scope_conditions=conditions).json()
    assert empty["items"] == [] and empty["retained_count"] == empty["qualified_count"] == 0
    assert empty["scope"]["scope_record_count"] == 0 and empty["scope"]["batch_record_count"] == 17
    assert all(d["mean"]["value"] is None for d in empty["scope"]["statistics"]["dimensions"])
    a = {"dimension_code": "algorithm_data_structure", "operator": "lte", "value": 2.75}
    b = {"dimension_code": "solution_strategy", "operator": "gte", "value": 4}
    x = call(client, batch, scope_conditions=[a, b]).json()
    y = call(client, batch, scope_conditions=[b, {**a, "value": 2.750}]).json()
    assert x["scope"]["input_key"] == y["scope"]["input_key"]
    assert x["scope"]["scope_record_count"] < 17


def test_teacher_reader_scope_and_revocation(client, runtime, formal_batch, bridge_http):
    _, batch, _ = formal_batch(17)
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    assert call(client, batch).status_code == 404
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    assert call(client, batch).status_code == 200
    runtime.admin.set_member(runtime.other, runtime.scope, can_import=False)
    assert call(client, batch, "sources").status_code == 200
    runtime.admin.revoke_read(runtime.other, batch["batch_id"])
    assert call(client, batch).status_code == 404
    runtime.admin.revoke_tokens(runtime.other)
    assert call(client, batch).status_code == 401
    client.headers.pop("Authorization")
    assert call(client, batch).status_code == 401


@pytest.mark.parametrize("change", ["token", "grant", "member", "expiry"])
def test_recheck_after_work_observes_revocation(client, runtime, formal_batch, bridge_http, change):
    from datetime import timedelta

    _, batch, _ = formal_batch(17)
    if change in ("grant", "member"):
        runtime.admin.grant_read(runtime.other, batch["batch_id"])
        client.headers["Authorization"] = "Bearer " + runtime.other_token
    hooks = {
        "token": lambda: runtime.admin.revoke_tokens(runtime.teacher),
        "grant": lambda: runtime.admin.revoke_read(runtime.other, batch["batch_id"]),
        "member": lambda: runtime.admin.remove_member(runtime.other, runtime.scope),
        "expiry": lambda: runtime.clock.__setitem__(0, runtime.clock[0] + timedelta(days=8)),
    }
    bridge_http["hook"] = hooks[change]
    response = call(client, batch)
    assert response.status_code == (404 if change in ("grant", "member") else 401)
    assert "items" not in response.json()


def test_snapshot_unknown_and_invalid_input_are_rejected(client, formal_batch, bridge_http):
    _, batch, _ = formal_batch(17)
    assert call(client, batch, snapshot_id="stale").status_code == 409
    assert call(client, batch, "unknown").status_code == 404
    for patch in (
        {"limit": True},
        {"limit": 51},
        {"offset": -1},
        {"profile": "qwen"},
        {"student_ids": ["forged"]},
        {
            "scope_conditions": [
                {"dimension_code": "algorithm_data_structure", "operator": "lt", "value": 2.751}
            ]
        },
    ):
        assert call(client, batch, **patch).status_code == 422


def test_sources_match_formal_http_evidence_and_full_counts(client, formal_batch, bridge_http):
    base, batch, _ = formal_batch(17)
    result = call(client, batch, "sources", limit=2).json()
    assert result["total_count"] == 17 and len(result["items"]) == 2 and result["page"]["has_more"]
    for member in result["items"]:
        assert len(member["scores"]) == 1
        score = member["scores"][0]
        proof = client.get(base + "/evidence/" + score["evidence_id"]).json()
        assert (proof["student_id"], proof["score"], proof["snapshot_id"]) == (
            member["student_id"],
            score["score"],
            batch["snapshot_id"],
        )
    for mode in ("foreign", "wrong-dimension"):
        bridge_http["mode"] = mode
        assert call(client, batch, "sources").status_code == 503


def test_damage_and_private_columns_never_become_discoveries(
    client, runtime, formal_batch, bridge_http, monkeypatch
):
    _, batch, _ = formal_batch(17)
    connect = runtime.db._connect

    def guarded():
        connection = connect()
        forbidden = {
            "student_name",
            "student_number",
            "comment_raw",
            "raw_xlsx",
            "text_cells_json",
            "identity_cell_raw",
        }
        connection.set_authorizer(
            lambda action, table, column, *rest: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_READ and column in forbidden
                else sqlite3.SQLITE_OK
            )
        )
        return connection

    monkeypatch.setattr(runtime.db, "_connect", guarded)
    assert call(client, batch).status_code == 200
    assert call(client, batch, "sources").status_code == 200
    monkeypatch.setattr(runtime.db, "_connect", connect)
    with runtime.db.transaction(write=True) as connection:
        connection.execute("DROP TRIGGER immutable_evaluations_update")
        connection.execute(
            "UPDATE evaluations SET source_row=999 WHERE batch_id=?", (batch["batch_id"],)
        )
    assert call(client, batch).status_code == 503


def test_budget_timeout_and_concurrency_rejection(client, formal_batch, bridge_http, monkeypatch):
    import app.edu.discovery_bridge as bridge

    _, batch, _ = formal_batch(17)
    monkeypatch.setattr(bridge, "MAX_RECORDS", 1)
    assert call(client, batch).status_code == 503
    monkeypatch.setattr(bridge, "MAX_RECORDS", 1666)
    monkeypatch.setattr(bridge, "MAX_SECONDS", -1)
    assert call(client, batch).json()["error"]["code"] == "DISCOVERY_TIMEOUT"
    monkeypatch.setattr(bridge, "MAX_SECONDS", 15)
    monkeypatch.setattr(bridge, "MAX_RESPONSE_BYTES", 1)
    assert call(client, batch).status_code == 413
    monkeypatch.setattr(bridge, "MAX_RESPONSE_BYTES", 1024 * 1024)
    assert bridge.SLOTS.acquire(blocking=False) and bridge.SLOTS.acquire(blocking=False)
    try:
        assert call(client, batch).json()["error"]["code"] == "DISCOVERY_BUSY"
    finally:
        bridge.SLOTS.release()
        bridge.SLOTS.release()


def test_approved_bridge_stays_independent_of_kernel_implementation(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/edu/batches/{batch_id}/discoveries/query" in paths
    import app.edu.discovery_bridge as bridge

    assert "app.analysis.edu.discovery" not in bridge.__dict__
