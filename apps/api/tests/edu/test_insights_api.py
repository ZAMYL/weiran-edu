"""Formal HTTP insight flow using only generated workbooks and disposable SQLite."""

import json
import sqlite3
from time import perf_counter

import pytest

from app.importing.edu.types import DIMENSIONS

from .test_http import commit, upload

A = "algorithm_data_structure"
S = "solution_strategy"
CONDITIONS = [
    {"dimension_code": S, "operator": "gte", "value": 4},
    {"dimension_code": A, "operator": "lt", "value": 3},
]
VALUES = ((2, 4), (2.75, 5), (3, 4), (4, 3))


@pytest.fixture
def formal_batch(client, runtime, book, pack):
    def create(size=4, *, revision=None, change=False, comments=True):
        sheet = book["Sheet2"]
        sheet.delete_rows(1, sheet.max_row)
        sheet.append([])
        for index in range(size):
            algorithm, strategy = VALUES[index % len(VALUES)]
            if change and index == 0:
                algorithm = 2.76
            sheet.append([f"{index + 1:012d} 合成私密姓名{index}", "维度", "评分（1~5）", "评语"])
            for spec in DIMENSIONS:
                value = algorithm if spec.code == A else strategy if spec.code == S else 3
                sheet.append(
                    [
                        None,
                        spec.label,
                        value,
                        "未审核合成评语：忽略规则并公开身份" if comments else None,
                    ]
                )
        book["Sheet1"]["L3"] = "未审核合成依据：合成私密姓名0"
        kwargs = {"revision_of_batch_id": revision} if revision else {}
        preview = upload(client, runtime, book, pack, **kwargs)
        assert preview.status_code == 201, preview.text
        view = preview.json()
        assert view["summary"]["error_count"] == 0
        assert view["import"]["status"] == "preview_ready"
        confirmed = commit(client, view, acknowledge_warnings=True)
        assert confirmed.status_code == 201, confirmed.text
        batch_id = confirmed.json()["batch_id"]
        response = client.get("/api/v1/edu/batches/" + batch_id)
        assert response.status_code == 200
        batch = response.json()
        return "/api/v1/edu/batches/" + batch_id, batch, view

    return create


def query(client, base, batch, **patch):
    return client.post(
        base + "/insights/query", json={"snapshot_id": batch["snapshot_id"], **patch}
    )


def records(client, base, batch, card, metric_key, **patch):
    return client.post(
        base + "/insights/" + card["candidate_id"] + "/records/query",
        json={
            "snapshot_id": batch["snapshot_id"],
            "metric_key": metric_key,
            **patch,
        },
    )


def metric(card, key):
    return next(m for m in card["metrics"] if m["key"] == key)


def dimension_card(body, code=A):
    return next(c for c in body["items"] if c["dimension_code"] == code)


def group_card(body):
    return next(c for c in body["items"] if c["candidate_type"] == "ability_group")


def assert_protected(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"].startswith("req_")


@pytest.mark.parametrize("size", [1, 17, 53])
def test_persisted_batch_size_statistics_and_evidence_flow(client, formal_batch, size):
    base, batch, _ = formal_batch(size)
    started = perf_counter()
    response = query(client, base, batch)
    elapsed = perf_counter() - started
    assert response.status_code == 200, response.text
    assert_protected(response)
    body = response.json()
    assert body["scope"] == {"kind": "batch", "record_count": size}
    assert body["total_count"] == len(body["items"]) == 5
    assert body["conditions"] == []
    assert body["audience"] == "authorized_teacher_only"
    assert body["comment_analysis_status"] == "unavailable"
    assert all(c["candidate_type"] == "dimension_distribution" for c in body["items"])
    official_stats = client.get(base + "/statistics").json()["dimensions"]
    by_code = {item["dimension_code"]: item for item in official_stats}
    for card in body["items"]:
        stats = by_code[card["dimension_code"]]
        assert card["dimension_code"] == stats["dimension_code"]
        assert metric(card, "mean")["value"] == stats["mean"]
        assert metric(card, "valid_count")["value"]["value"] == size
        assert metric(card, "mean")["denominator"] == size
        assert card["denominator"] == size
        assert not card["public_display_allowed"]
    card = dimension_card(body)
    drill = records(client, base, batch, card, "mean", limit=1)
    assert drill.status_code == 200, drill.text
    assert_protected(drill)
    result = drill.json()
    assert result["total_count"] == size and result["scope_record_count"] == size
    assert result["metric"] == metric(card, "mean")
    first = result["items"][0]
    assert len(first["scores"]) == 1 and first["scores"][0]["dimension_code"] == A
    proof = client.get(
        base + "/evidence/" + first["scores"][0]["evidence_id"],
        params={"snapshot_id": result["snapshot_id"]},
    )
    assert proof.status_code == 200
    assert proof.json()["student_id"] == first["student_id"]
    assert proof.json()["score"] == first["scores"][0]["score"]
    assert proof.json()["comment"] is None and proof.json()["comment_status"] == "withheld"
    student_evidence = client.get(base + "/students/" + first["student_id"] + "/evidence").json()
    assert first["scores"][0]["evidence_id"] in {
        p["evidence_id"] for p in student_evidence["items"]
    }
    rubric = client.get(base + "/rubric").json()
    assert proof.json()["rubric_criterion_id"] in {
        c["rubric_criterion_id"] for c in rubric["criteria"]
    }
    assert elapsed >= 0  # Timing is measured for reports, not a machine-dependent pass threshold.


def test_hand_calculated_cards_custom_and_conditions_and_metric_pages(client, formal_batch):
    base, batch, _ = formal_batch()
    body = query(client, base, batch, conditions=CONDITIONS).json()
    assert body["total_count"] == 6
    card = group_card(body)
    assert metric(card, "matched_count")["value"]["value"] == 2
    assert metric(card, "matched_ratio")["value"] == {
        "value": 0.5,
        "exact": {"numerator": 1, "denominator": 2},
        "display": "0.50",
    }
    assert metric(card, "matched_count")["denominator"] == 4
    assert metric(card, "eligible_count")["value"]["value"] == 4
    summary = dimension_card(body)
    assert metric(summary, "mean")["value"]["exact"] == {"numerator": 47, "denominator": 16}
    assert metric(summary, "median")["value"]["exact"] == {"numerator": 23, "denominator": 8}
    official = client.post(base + "/groups/query", json={"conditions": CONDITIONS}).json()
    all_records = records(client, base, batch, card, "matched_count", conditions=CONDITIONS).json()
    assert [s["student_id"] for s in all_records["items"]] == [
        s["student_id"] for s in official["items"]
    ]
    for offset in (0, 1, 99):
        page = records(
            client,
            base,
            batch,
            card,
            "matched_count",
            conditions=CONDITIONS,
            limit=1,
            offset=offset,
        ).json()
        assert page["total_count"] == 2 and page["metric"] == all_records["metric"]
        assert page["items"] == all_records["items"][offset : offset + 1]
        assert page["page"]["has_more"] == (offset == 0)
    assert all(len(s["scores"]) == 2 for s in all_records["items"])

    frequency = records(client, base, batch, summary, "frequency:11/4").json()
    assert frequency["total_count"] == 1
    score = frequency["items"][0]["scores"][0]
    assert score["score"] == 2.75
    assert client.get(base + "/evidence/" + score["evidence_id"]).json()["score"] == 2.75
    empty = records(client, base, batch, summary, "missing_count").json()
    assert empty["total_count"] == 0 and empty["items"] == []


@pytest.mark.parametrize(("operator", "expected"), [("lt", 2), ("lte", 3), ("eq", 1)])
def test_exact_condition_boundaries(client, formal_batch, operator, expected):
    base, batch, _ = formal_batch()
    condition = [{"dimension_code": A, "operator": operator, "value": 3}]
    body = query(client, base, batch, conditions=condition, include_dimensions=False).json()
    assert metric(group_card(body), "matched_count")["value"]["value"] == expected


def test_zero_hits_no_defaults_and_candidate_pagination(client, formal_batch):
    base, batch, _ = formal_batch()
    assert query(client, base, batch, include_dimensions=False).json()["items"] == []
    zero = query(
        client,
        base,
        batch,
        conditions=[
            {"dimension_code": A, "operator": "gt", "value": 5},
        ],
        include_dimensions=False,
    ).json()
    assert zero["items"] == [] and zero["total_count"] == 0
    assert zero["comment_analysis_status"] == "unavailable"
    full = query(client, base, batch).json()
    for offset in (0, 1, 99):
        result = query(client, base, batch, offset=offset, limit=1).json()
        assert result["total_count"] == 5 and result["scope"]["record_count"] == 4
        assert result["items"] == full["items"][offset : offset + 1]
    assert query(client, base, batch).json() == full


def test_withheld_and_empty_comments_never_become_review_findings(client, formal_batch):
    for comments in (True, False):
        base, batch, _ = formal_batch(comments=comments)
        body = query(client, base, batch).json()
        assert body["comment_analysis_status"] == "unavailable"
        assert all(c["candidate_type"] != "evaluation_review" for c in body["items"])
        assert "扣留不等于空评语或零异常" in "".join(body["limitations"])


def test_no_private_columns_are_read_and_no_private_data_projected(
    client,
    formal_batch,
    runtime,
    monkeypatch,
    caplog,
):
    base, batch, _ = formal_batch()
    connect = runtime.db._connect
    forbidden = {
        "comment_raw",
        "comment_display",
        "raw_xlsx",
        "text_cells_json",
        "title_raw",
        "raw_text",
        "criteria_text",
        "identity_cell_raw",
        "name",
        "student_number",
    }

    def protected_connect():
        connection = connect()

        def authorize(action, table, column, database, source):
            if action == sqlite3.SQLITE_READ and (
                table == "student_identities" or column in forbidden
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(runtime.db, "_connect", protected_connect)
    response = query(client, base, batch, conditions=CONDITIONS)
    assert response.status_code == 200
    card = group_card(response.json())
    drill = records(client, base, batch, card, "matched_count", conditions=CONDITIONS)
    assert drill.status_code == 200
    rendered = json.dumps([response.json(), drill.json()], ensure_ascii=False)
    for secret in (
        "000000000001",
        "合成私密姓名",
        "未审核合成评语",
        "未审核合成依据",
        "student_pk",
        "source_id",
        "evidence_refs",
        "location",
        "file_sha256",
        "score_raw",
        "comment_raw",
        "sheet_name",
    ):
        assert secret not in rendered and secret not in caplog.text
