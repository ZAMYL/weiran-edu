"""Real formal discovery HTTP: synthetic Excel, actual spawned engine, SQLite and auth."""

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

import pytest

from app.edu import discovery_bridge, discovery_worker
from app.edu.discovery_bridge import DiscoveryInputReader
from app.importing.edu.types import DIMENSIONS

from .test_http import commit, upload
from .test_insights_api import formal_batch as formal_batch


@pytest.fixture
def custom_batch(client, runtime, book, pack):
    def create(rows):
        sheet = book["Sheet2"]
        sheet.delete_rows(1, sheet.max_row)
        sheet.append([])
        for index, values in enumerate(rows):
            sheet.append([f"{index + 1:012d} 合成占位{index}", "维度", "评分（1~5）", "评语"])
            for spec, value in zip(DIMENSIONS, values, strict=True):
                sheet.append([None, spec.label, value, "未审核合成私密评语"])
        staged = upload(client, runtime, book, pack)
        assert staged.status_code == 201, staged.text
        confirmed = commit(client, staged.json(), acknowledge_warnings=True)
        assert confirmed.status_code == 201, confirmed.text
        batch_id = confirmed.json()["batch_id"]
        base = "/api/v1/edu/batches/" + batch_id
        batch = client.get(base).json()
        return base, batch

    return create


def query(client, base, batch, suffix="query", **values):
    return client.post(
        base + "/discoveries/" + suffix, json={"snapshot_id": batch["snapshot_id"], **values}
    )


def test_real_list_detail_chart_and_metric_evidence(client, formal_batch):
    base, batch, _ = formal_batch(53)
    response = query(client, base, batch, limit=1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"]["batch_record_count"] == body["scope"]["scope_record_count"] == 53
    assert body["rule_version"] == "edu-discovery-0.1.1-draft"
    assert body["retained_count"] > 0 and len(body["items"]) == 1
    mean = body["scope"]["statistics"]["dimensions"][0]["mean"]["exact"]
    assert Fraction(mean["numerator"], mean["denominator"]) == Fraction(619, 212)
    first = body["items"][0]
    detail = query(client, base, batch, first["discovery_id"] + "/query")
    assert detail.status_code == 200, detail.text
    finding = detail.json()
    assert finding["chart"] and not finding["public_display_allowed"]
    assert finding["input_key"] == body["scope"]["input_key"]
    chart = query(client, base, batch, first["discovery_id"] + "/chart/query")
    assert chart.status_code == 200, chart.text
    assert chart.json()["chart"] == finding["chart"]
    assert chart.json()["metrics"] == finding["metrics"]
    metric = finding["controlled_queries"][0]["metric_key"]
    records = query(
        client, base, batch, first["discovery_id"] + "/records/query", metric_key=metric, limit=2
    )
    assert records.status_code == 200, records.text
    assert records.json()["total_count"] >= len(records.json()["items"]) > 0
    for student in records.json()["items"]:
        for score in student["scores"]:
            proof = client.get(
                base + "/evidence/" + score["evidence_id"],
                params={"snapshot_id": batch["snapshot_id"]},
            )
            assert proof.status_code == 200
            assert proof.json()["student_id"] == student["student_id"]
            assert proof.json()["dimension_code"] == score["dimension_code"]
            assert proof.json()["score"] == score["score"]
    for result in (response, detail, chart, records):
        assert result.headers["cache-control"] == "no-store"
        assert result.headers["x-request-id"].startswith("req_")
        assert "合成私密姓名" not in result.text
        assert "未审核合成" not in result.text


def test_full_statistics_and_retained_counts_are_invariant_under_paging(client, formal_batch):
    base, batch, _ = formal_batch(53)
    full = query(client, base, batch, limit=50).json()
    assert len(full["items"]) == full["retained_count"]
    for offset in (1, 999):
        page = query(client, base, batch, offset=offset, limit=1)
        assert page.status_code == 200, page.text
        page = page.json()
        assert page["scope"] == full["scope"]
        assert page["qualified_count"] == full["qualified_count"]
        assert page["retained_count"] == full["retained_count"]
        assert page["items"] == full["items"][offset : offset + 1]
        assert page["page"]["has_more"] == (offset + 1 < full["retained_count"])
    first = full["items"][0]
    detail = query(client, base, batch, first["discovery_id"] + "/query").json()
    key = detail["controlled_queries"][0]["metric_key"]
    one = query(
        client, base, batch, first["discovery_id"] + "/records/query", metric_key=key, limit=1
    ).json()
    two = query(
        client,
        base,
        batch,
        first["discovery_id"] + "/records/query",
        metric_key=key,
        offset=999,
        limit=1,
    ).json()
    assert one["total_count"] == two["total_count"] and two["items"] == []
    assert one["scope"] == two["scope"]


def test_scope_and_inner_conditions_are_separate_and_boundary_chart_is_real(client, formal_batch):
    base, batch, _ = formal_batch(53)
    conditions = [{"dimension_code": "algorithm_data_structure", "operator": "lte", "value": 3}]
    full = query(client, base, batch).json()
    response = query(client, base, batch, scope_conditions=conditions, limit=50)
    assert response.status_code == 200, response.text
    scoped = response.json()
    assert scoped["scope"]["batch_record_count"] == 53
    assert scoped["scope"]["scope_record_count"] == sum(i % 4 != 3 for i in range(53))
    assert scoped["scope"]["scope_conditions"] == conditions
    assert scoped["scope"]["input_key"] != full["scope"]["input_key"]
    boundary = next(f for f in scoped["items"] if f["type"] == "boundary_sensitivity")
    detail = query(
        client, base, batch, boundary["discovery_id"] + "/query", scope_conditions=conditions
    ).json()
    assert detail["metrics"]["student_denominator"] == scoped["scope"]["scope_record_count"]
    assert detail["metrics"]["added_count"] == sum(i % 4 == 2 for i in range(53))
    assert {point["operator"] for point in detail["chart"]} == {"lt", "lte"}
    assert detail["conditions"][0]["dimension_code"] == "algorithm_data_structure"
    added = query(
        client,
        base,
        batch,
        boundary["discovery_id"] + "/records/query",
        scope_conditions=conditions,
        metric_key="added_members",
        limit=50,
    )
    assert added.status_code == 200, added.text
    assert added.json()["total_count"] == detail["metrics"]["added_count"]
    assert all(s["scores"][0]["score"] == 3 for s in added.json()["items"])
    # An ID never silently falls back to the whole batch or another range.
    assert query(client, base, batch, boundary["discovery_id"] + "/query").status_code == 404


def test_empty_scope_no_findings_unknown_id_and_metric(client, formal_batch):
    base, batch, _ = formal_batch(1)
    body = query(client, base, batch).json()
    assert body["items"] == [] and body["qualified_count"] == body["retained_count"] == 0
    assert query(client, base, batch, "f" * 24 + "/query").status_code == 404
    empty = query(
        client,
        base,
        batch,
        scope_conditions=[
            {"dimension_code": "algorithm_data_structure", "operator": "lt", "value": 1}
        ],
    )
    assert empty.status_code == 200 and empty.json()["scope"]["scope_record_count"] == 0
    base, batch, _ = formal_batch(17, revision=batch["batch_id"], change=True)
    first = query(client, base, batch).json()["items"][0]
    response = query(
        client, base, batch, first["discovery_id"] + "/records/query", metric_key="unknown_metric"
    )
    assert response.status_code == 404 and "items" not in response.json()


def test_owner_readonly_grant_revocation_and_auth_before_json(client, runtime, formal_batch):
    base, batch, _ = formal_batch(17)
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    assert query(client, base, batch).status_code == 404
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    runtime.admin.set_member(runtime.other, runtime.scope, can_import=False)
    response = query(client, base, batch)
    assert response.status_code == 200, response.text
    first = response.json()["items"][0]["discovery_id"]
    detail = query(client, base, batch, first + "/query").json()
    key = detail["controlled_queries"][0]["metric_key"]
    assert query(client, base, batch, first + "/records/query", metric_key=key).status_code == 200
    for suffix in (first + "/query", first + "/chart/query"):
        assert query(client, base, batch, suffix).status_code == 200
    runtime.admin.revoke_read(runtime.other, batch["batch_id"])
    for suffix in ("query", first + "/query", first + "/chart/query"):
        assert query(client, base, batch, suffix).status_code == 404
    assert (
        query(client, base, batch, first + "/records/query", metric_key="paired_scores").status_code
        == 404
    )
    client.headers.pop("Authorization")
    response = client.post(base + "/discoveries/query", content="invalid json")
    assert response.status_code == 401


@pytest.mark.parametrize("change", ["token", "grant", "member", "expiry"])
def test_revocation_during_real_computation_never_returns_results(
    client, runtime, formal_batch, monkeypatch, change
):
    base, batch, _ = formal_batch(17)
    if change in {"grant", "member"}:
        runtime.admin.grant_read(runtime.other, batch["batch_id"])
        client.headers["Authorization"] = "Bearer " + runtime.other_token
    execute = discovery_worker.execute

    async def mutate_after_compute(*args, **kwargs):
        result = await execute(*args, **kwargs)
        if change == "token":
            runtime.admin.revoke_tokens(runtime.teacher)
        elif change == "grant":
            runtime.admin.revoke_read(runtime.other, batch["batch_id"])
        elif change == "member":
            runtime.admin.remove_member(runtime.other, runtime.scope)
        else:
            runtime.clock[0] += timedelta(days=8)
        return result

    monkeypatch.setattr(discovery_worker, "execute", mutate_after_compute)
    response = query(client, base, batch)
    assert response.status_code == (404 if change in {"grant", "member"} else 401)
    assert "items" not in response.json()


def test_scope_snapshot_request_and_response_budgets_are_distinct(
    client, formal_batch, monkeypatch
):
    base, batch, _ = formal_batch(17)
    assert query(client, base, batch, snapshot_id="stale").status_code == 409
    for values in (
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
        assert query(client, base, batch, **values).status_code == 422
    oversized = client.post(
        base + "/discoveries/query",
        content='{"x":"' + "x" * 65536 + '"}',
        headers={"content-type": "application/json"},
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    monkeypatch.setattr(discovery_bridge, "MAX_RESPONSE_BYTES", 1)
    response = query(client, base, batch)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "DISCOVERY_RESPONSE_TOO_LARGE"


def test_private_columns_and_damage_are_rejected_without_model_calls(
    client, runtime, formal_batch, monkeypatch, caplog
):
    base, batch, _ = formal_batch(17)
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
            "raw_text",
            "criteria_text",
        }
        connection.set_authorizer(
            lambda action, table, column, *rest: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_READ
                and (column in forbidden or table == "student_identities")
                else sqlite3.SQLITE_OK
            )
        )
        return connection

    monkeypatch.setattr(runtime.db, "_connect", guarded)
    response = query(client, base, batch)
    assert response.status_code == 200, response.text
    for secret in (
        "合成私密姓名",
        "未审核合成",
        "file_sha256",
        "source_id",
        "evidence_refs",
        "location",
    ):
        assert secret not in response.text and secret not in caplog.text
    monkeypatch.setattr(runtime.db, "_connect", connect)
    with runtime.db.transaction(write=True) as connection:
        connection.execute("DROP TRIGGER immutable_evaluations_update")
        connection.execute(
            "UPDATE evaluations SET source_row=999 WHERE batch_id=?", (batch["batch_id"],)
        )
    response = query(client, base, batch)
    assert (
        response.status_code == 503 and response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"
    )


def test_wrong_child_snapshot_is_not_accepted(client, formal_batch, monkeypatch):
    base, batch, _ = formal_batch(17)
    execute = discovery_worker.execute

    async def wrong(*args, **kwargs):
        result = await execute(*args, **kwargs)
        return replace(result, context=replace(result.context, snapshot_id="wrong"))

    monkeypatch.setattr(discovery_worker, "execute", wrong)
    assert query(client, base, batch).status_code == 503


def test_all_sources_are_verified_before_paging_and_kernel_rejects_forgery(
    client, runtime, formal_batch, monkeypatch
):
    base, batch, _ = formal_batch(17)
    details = query(client, base, batch).json()
    first = details["items"][0]["discovery_id"]
    detail = query(client, base, batch, first + "/query").json()
    key = detail["controlled_queries"][0]["metric_key"]
    original = DiscoveryInputReader.resolve_sources
    seen = []

    def inspect(self, actor, data, refs, dimensions, **kwargs):
        seen.append(len(refs))
        return original(self, actor, data, refs, dimensions, **kwargs)

    monkeypatch.setattr(DiscoveryInputReader, "resolve_sources", inspect)
    response = query(client, base, batch, first + "/records/query", metric_key=key, limit=1)
    assert response.status_code == 200
    assert seen == [response.json()["total_count"]]
    assert seen[0] > len(response.json()["items"])
    # A forged internal source must fail in the kernel before the DB resolver runs.
    execute = discovery_worker.execute

    async def forge(*args, **kwargs):
        result = await execute(*args, **kwargs)
        finding = next(f for f in result.findings if f.discovery_id == first)
        source = finding.sources[0]
        changed = replace(source, records=source.records[:-1])
        finding = replace(finding, sources=(changed, *finding.sources[1:]))
        return replace(
            result,
            findings=tuple(finding if f.discovery_id == first else f for f in result.findings),
        )

    seen.clear()
    monkeypatch.setattr(discovery_worker, "execute", forge)
    assert (
        query(client, base, batch, first + "/records/query", metric_key=key, limit=1).status_code
        == 503
    )
    assert seen == []


def test_four_authenticated_discovery_operations_are_documented(client):
    schema = client.get("/openapi.json").json()
    prefix = "/api/v1/edu/batches/{batch_id}/discoveries"
    for suffix in (
        "/query",
        "/{discovery_id}/query",
        "/{discovery_id}/chart/query",
        "/{discovery_id}/records/query",
    ):
        operation = schema["paths"][prefix + suffix]["post"]
        assert operation["security"] == [{"HTTPBearer": []}]
        assert "application/json" in operation["requestBody"]["content"]
        assert all(str(code) in operation["responses"] for code in (401, 404, 409, 413, 422, 503))
    rendered = json.dumps(schema)
    assert "DiscoveryDetail" in rendered and "DiscoveryChart" in rendered


def test_fixed_group_omission_is_repaired_through_actual_excel_http(client, custom_batch):
    base, batch = custom_batch([(1, 2.90, 3, 3, 3)] * 10 + [(5, 3.10, 3, 3, 3)] * 10)
    result = query(client, base, batch, limit=50)
    assert result.status_code == 200, result.text
    groups = [f for f in result.json()["items"] if f["type"] == "group_difference"]
    assert len(groups) == 1
    key = groups[0]["discovery_id"]
    detail = query(client, base, batch, key + "/query").json()
    assert detail["metrics"]["outcome_dimension"] == "algorithm_data_structure"
    assert detail["metrics"]["target_count"] == detail["metrics"]["remainder_count"] == 10
    outcome = next(p for p in detail["chart"] if p["dimension_code"] == "algorithm_data_structure")
    assert outcome["difference"]["numerator"] == -4
    assert outcome["difference"]["denominator"] == 1
    members = query(
        client, base, batch, key + "/records/query", metric_key="target.ALGORITHMS", limit=50
    ).json()
    assert members["total_count"] == 10
    assert all(p["scores"][0]["score"] == 1 for p in members["items"])


def test_rare_members_are_distinct_from_full_pair_reference(client, custom_batch):
    base, batch = custom_batch([(3, 3, 3, 3, 3)] * 16 + [(5, 1, 3, 3, 3)])
    result = query(client, base, batch, limit=50)
    assert result.status_code == 200, result.text
    rare = next(f for f in result.json()["items"] if f["type"] == "rare_combination")
    key = rare["discovery_id"]
    detail = query(client, base, batch, key + "/query").json()
    assert detail["metrics"]["match_count"] == 1
    assert detail["metrics"]["pair_count"] == detail["metrics"]["student_denominator"] == 17
    assert sum(p["count"] for p in detail["chart"]) == 17
    rare_members = query(
        client, base, batch, key + "/records/query", metric_key="rare_members"
    ).json()
    reference = query(
        client, base, batch, key + "/records/query", metric_key="pair_reference", limit=50
    ).json()
    assert rare_members["total_count"] == 1 and reference["total_count"] == 17
    assert len(rare_members["items"][0]["scores"]) == 2
    other_base, other_batch = custom_batch([(2, 2, 2, 2, 2)] * 17)
    assert query(client, other_base, other_batch, key + "/query").status_code == 404


def test_no_partial_result_when_database_changes_after_compute(
    client, runtime, formal_batch, monkeypatch
):
    base, batch, _ = formal_batch(17)
    execute = discovery_worker.execute

    async def corrupt(*args, **kwargs):
        result = await execute(*args, **kwargs)
        with runtime.db.transaction(write=True) as connection:
            connection.execute("DROP TRIGGER immutable_evaluations_update")
            connection.execute(
                "UPDATE evaluations SET source_row=999 WHERE batch_id=?", (batch["batch_id"],)
            )
        return result

    monkeypatch.setattr(discovery_worker, "execute", corrupt)
    response = query(client, base, batch)
    assert response.status_code == 503 and "items" not in response.json()


def test_shape_mismatches_cannot_masquerade_as_another_finding_type(
    client, formal_batch, monkeypatch
):
    base, batch, _ = formal_batch(17)
    execute = discovery_worker.execute

    async def forge(*args, **kwargs):
        result = await execute(*args, **kwargs)
        f = result.findings[0]
        kind = "boundary_sensitivity" if f.type != "boundary_sensitivity" else "dimension_relation"
        return replace(result, findings=(replace(f, type=kind), *result.findings[1:]))

    monkeypatch.setattr(discovery_worker, "execute", forge)
    assert query(client, base, batch).status_code == 503


def test_real_kernel_retention_cap_is_distinct_from_page_and_qualified_count(client, custom_batch):
    # Three common score levels create distinct low/high partitions in all five
    # dimensions and more qualified facts than the fixed 50-result retention cap.
    base, batch = custom_batch(
        [(1, 1, 1, 1, 1)] * 10 + [(3, 3, 3, 3, 3)] * 10 + [(5, 5, 5, 5, 5)] * 10
    )
    full = query(client, base, batch, limit=50)
    assert full.status_code == 200, full.text
    full = full.json()
    assert full["retained_count"] == len(full["items"]) == 50
    assert full["qualified_count"] > 50 and full["truncated"]
    assert full["scope"]["scope_record_count"] == 30
    last = query(client, base, batch, offset=49, limit=1).json()
    beyond = query(client, base, batch, offset=50, limit=1).json()
    assert last["items"] == full["items"][49:] and not last["page"]["has_more"]
    assert beyond["items"] == [] and not beyond["page"]["has_more"]
    assert beyond["qualified_count"] == last["qualified_count"] == full["qualified_count"]
    assert beyond["scope"]["statistics"] == full["scope"]["statistics"]
