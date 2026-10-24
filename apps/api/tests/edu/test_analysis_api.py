"""Synthetic end-to-end proofs of counts, precision, authorization and provenance."""

import json
from fractions import Fraction

import pytest

from app.core.errors import APIError
from app.edu.analysis import AnalysisReader, stat
from app.edu.analysis_models import GroupQuery
from app.importing.edu.types import DIMENSIONS

from .conftest import commit

A = "algorithm_data_structure"
B = "code_implementation"


def condition(code=A, operator="gte", value=2.75):
    return {"dimension_code": code, "operator": operator, "value": value}


@pytest.fixture
def cohort(runtime, book, stage):
    sheet = book["Sheet2"]
    sheet.delete_rows(2, sheet.max_row)
    sheet["A1"] = None  # Preserve the template's empty first row before appending.
    scores = (
        (1, 5, 2.67, 1.01, 4),
        (2.75, 3, 2.68, 2.02, 4),
        (2.76, 2, 2.67, 3.03, 4),
        (5, 1, 2.68, 4.04, 4),
    )
    for index, values in enumerate(scores):
        sheet.append([f"{index + 1:012d} 合成姓名{index}", "维度", "评分（1~5）", "评语"])
        for offset, (spec, value) in enumerate(zip(DIMENSIONS, values, strict=True)):
            sheet.append([None, spec.label, value, f"合成评语 {index}:{offset}"])
    book["Sheet1"]["L3"] = "未审查的依据原文，合成姓名0，000000000001"
    batch_id = commit(runtime, stage()).batch_id
    batch = runtime.service.get_batch(runtime.p, batch_id)
    return f"/api/v1/edu/batches/{batch_id}", batch


def test_statistics_exact_rounding_and_sparse_matrix(client, cohort):
    url, batch = cohort
    response = client.get(
        url + "/statistics",
        params={"snapshot_id": batch.snapshot_id, "x_dimension": A, "y_dimension": B},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["scope"] == {"kind": "batch", "record_count": 4}
    assert result["snapshot_id"] == batch.snapshot_id
    assert result["display_policy"] == {"digits": 2, "rounding": "HALF_UP"}
    assert [d["dimension_code"] for d in result["dimensions"]] == [s.code for s in DIMENSIONS]
    a, _, modeling, _, strategy = result["dimensions"]
    assert a["mean"] == {
        "value": 2.8775,
        "exact": {"numerator": 1151, "denominator": 400},
        "display": "2.88",
    }
    assert a["median"]["exact"] == {"numerator": 551, "denominator": 200}
    assert a["median"]["display"] == "2.76"
    assert modeling["mean"]["display"] == "2.68"  # Exact 2.675 HALF_UP.
    assert modeling["mean"]["exact"] == {"numerator": 107, "denominator": 40}
    assert a["minimum"]["value"] == 1 and a["maximum"]["value"] == 5
    for d in result["dimensions"]:
        assert (d["valid_count"], d["missing_count"], d["invalid_count"]) == (4, 0, 0)
        assert sum(f["count"] for f in d["distribution"]) == 4
        assert {f["denominator"] for f in d["distribution"]} == {4}
    assert a["distribution"][1]["score"] == 2.75
    assert a["distribution"][1]["ratio"]["exact"] == {"numerator": 1, "denominator": 4}
    assert strategy["distribution"][0]["ratio"]["value"] == 1
    matrix = result["matrix"]
    assert matrix["scope"] == "batch" and matrix["excluded_count"] == 0
    assert matrix["paired_count"] == sum(c["count"] for c in matrix["cells"]) == 4
    assert matrix["x_values"] == [1, 2.75, 2.76, 5]
    assert matrix["y_values"] == [1, 2, 3, 5]
    assert matrix["cells"][1] == {"x": 2.75, "y": 3, "count": 1}
    assert client.get(url + "/statistics").json()["matrix"] is None


def test_group_denominators_paging_and_evidence_chain(client, cohort):
    url, batch = cohort
    query = {
        "conditions": [condition(), condition(B, "gte", 2)],
        "limit": 1,
        "include_statistics": True,
        "snapshot_id": batch.snapshot_id,
    }
    response = client.post(url + "/groups/query", json=query)
    assert response.status_code == 200
    result = response.json()
    assert result["matched_count"] == 2 and result["total_count"] == 4
    assert result["eligible_count"] == 4 and result["unevaluable_count"] == 0
    assert result["ratio"] == result["matched_among_eligible_ratio"] == 0.5
    assert result["page"]["has_more"] is True and len(result["items"]) == 1
    assert result["conditions"] == query["conditions"]
    stats = result["statistics"]
    assert stats["scope"] == {"kind": "matched_group", "record_count": 2}
    assert stats["matrix"] is None
    assert stats["dimensions"][0]["mean"]["exact"] == {"numerator": 551, "denominator": 200}
    assert stats["dimensions"][0]["mean"]["display"] == "2.76"
    assert {f["denominator"] for f in stats["dimensions"][0]["distribution"]} == {2}
    first = result["items"][0]
    all_students = client.get(url + "/students").json()["items"]
    assert first == all_students[1]
    second_page = client.post(url + "/groups/query", json={**query, "offset": 1}).json()
    assert second_page["items"] == [all_students[2]]
    assert second_page["page"]["has_more"] is False
    assert second_page["statistics"] == stats  # Statistics precede pagination.
    beyond = client.post(url + "/groups/query", json={**query, "offset": 99}).json()
    assert beyond["items"] == [] and beyond["matched_count"] == 2
    assert beyond["statistics"] == stats
    evidence_id = first["scores"][0]["evidence_id"]
    proof = client.get(
        url + "/evidence/" + evidence_id, params={"snapshot_id": result["snapshot_id"]}
    ).json()
    assert proof["student_id"] == first["student_id"]
    assert proof["dimension_code"] == A and proof["score"] == 2.75
    assert proof["score_raw"] == "2.75"
    assert proof["source"]["sheet_name"] == "Sheet2"
    assert proof["source"]["score_cell"] == "C9"
    assert proof["source"]["file_sha256"] == batch.file_sha256
    assert proof["snapshot_id"] == batch.snapshot_id
    assert proof["comment"] is None and proof["comment_status"] == "withheld"
    profile = client.get(url + "/students/" + first["student_id"] + "/evidence").json()
    assert profile["snapshot_id"] == batch.snapshot_id and len(profile["items"]) == 5
    assert profile["items"][0] == proof
    rubric = client.get(url + "/rubric", params={"snapshot_id": batch.snapshot_id}).json()
    criterion = next(c for c in rubric["criteria"] if c["dimension_code"] == A)
    assert criterion["rubric_criterion_id"] == proof["rubric_criterion_id"]
    assert criterion["source"] == {
        "sheet_name": "Sheet1",
        "range": "L3:P6",
        "file_sha256": batch.file_sha256,
    }
    assert criterion["criteria_text"] is None and criterion["criteria_status"] == "withheld"
    serialized = json.dumps([result, proof, profile, rubric], ensure_ascii=False)
    for private in ["000000000001", "合成姓名", "合成评语", "未审查的依据原文", "student_pk"]:
        assert private not in serialized


@pytest.mark.parametrize(
    "operator,value,count",
    [
        ("lt", 2.75, 1),
        ("lte", 2.75, 2),
        ("eq", 2.75, 1),
        ("gte", 2.75, 3),
        ("gt", 2.75, 2),
        ("eq", 2.76, 1),
        ("gte", 1, 4),
        ("lte", 5, 4),
        ("gt", 5, 0),
        ("lt", 1, 0),
    ],
)
def test_threshold_operators(client, cohort, operator, value, count):
    url, _ = cohort
    response = client.post(
        url + "/groups/query",
        json={
            "conditions": [condition(operator=operator, value=value)],
            "include_statistics": True,
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["matched_count"] == len(result["items"]) == count
    assert result["total_count"] == 4 and result["ratio"] == count / 4
    assert result["statistics"]["scope"]["record_count"] == count
    if count == 0:
        assert result["statistics"]["dimensions"][0]["mean"] == {
            "value": None,
            "exact": None,
            "display": "暂无有效数据",
        }
        assert result["statistics"]["dimensions"][0]["distribution"] == []


@pytest.mark.parametrize("value", ["2.75", True, None, 0.99, 5.01, 2.751])
def test_reject_invalid_thresholds(client, cohort, value):
    url, _ = cohort
    response = client.post(url + "/groups/query", json={"conditions": [condition(value=value)]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "raw",
    [
        '{"conditions":[{"dimension_code":"algorithm_data_structure","operator":"gte",'
        '"value":2.750000000000000000000000000001}]}',
        '{"conditions":[{"dimension_code":"algorithm_data_structure","operator":"gte",'
        '"value":NaN}]}',
        '{"conditions":[],"conditions":[{}]}',
        '{"conditions":[{"value":2.75,"value":3}]}',
        '{"conditions":[{"dimension_code":"algorithm_data_structure","operator":"eq",'
        '"value":1e999999999999999999999999999999}]}',
        '{"conditions":[{"dimension_code":"algorithm_data_structure","operator":"eq",'
        '"value":Infinity}]}',
        "[]",
        "{",
        '{"conditions":' + "[" * 1100,
    ],
)
def test_raw_json_is_exact_and_unambiguous(client, cohort, raw):
    url, _ = cohort
    response = client.post(
        url + "/groups/query", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert "2.750000000000" not in response.text


@pytest.mark.parametrize(
    "patch",
    [
        {"conditions": []},
        {"conditions": [condition()] * 2},
        {"conditions": [condition(), condition(B), condition("solution_strategy")]},
        {"conditions": [condition("unknown")]},
        {"conditions": [condition(operator=">=")]},
        {"conditions": [{**condition(), "private": "never echo"}]},
        {"offset": -1},
        {"offset": True},
        {"limit": 0},
        {"limit": 101},
        {"limit": "20"},
        {"include_statistics": "true"},
        {"snapshot_id": ""},
        {"student_ids": []},
        {"sql": "SELECT student_name FROM student_identities"},
    ],
)
def test_query_shape_is_closed(client, cohort, patch):
    url, _ = cohort
    response = client.post(url + "/groups/query", json={"conditions": [condition()], **patch})
    assert response.status_code == 422
    assert "never echo" not in response.text and "SELECT" not in response.text


@pytest.mark.parametrize(
    "params",
    [
        {"x_dimension": A},
        {"y_dimension": B},
        {"x_dimension": A, "y_dimension": A},
        {"x_dimension": "unknown", "y_dimension": B},
    ],
)
def test_matrix_requires_distinct_pair(client, cohort, params):
    url, _ = cohort
    assert client.get(url + "/statistics", params=params).status_code == 422


def test_json_limits_and_numeric_exponent(client, cohort):
    url, _ = cohort
    assert (
        client.post(
            url + "/groups/query", content="{}", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert (
        client.post(
            url + "/groups/query",
            content=b" " * (65536 + 1),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
    raw = (
        '{"conditions":[{"dimension_code":"algorithm_data_structure",'
        '"operator":"eq","value":275e-2}]}'
    )
    response = client.post(
        url + "/groups/query",
        content=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert response.status_code == 200
    assert response.json()["matched_count"] == 1 and response.json()["statistics"] is None


def test_authentication_precedes_group_body_decoding(client, monkeypatch):
    async def forbidden_decode(_):
        raise AssertionError("Unauthenticated body was read")

    monkeypatch.setattr("app.routers.edu.group_query", forbidden_decode)
    client.headers.pop("Authorization")
    assert client.post("/api/v1/edu/batches/unknown/groups/query", content=b"{").status_code == 401


def test_streamed_query_bound_ignores_declared_length(client, cohort):
    url, _ = cohort

    def chunks():
        yield b" " * 32768
        yield b" " * 32769

    response = client.post(
        url + "/groups/query",
        content=chunks(),
        headers={"Content-Type": "application/json", "Content-Length": "2"},
    )
    assert response.status_code == 413


def test_all_reads_enforce_live_permissions_and_snapshots(client, cohort, runtime):
    url, batch = cohort
    student = client.get(url + "/students").json()["items"][0]
    targets = [
        "/statistics",
        "/rubric",
        "/evidence/" + student["scores"][0]["evidence_id"],
        "/students/" + student["student_id"] + "/evidence",
    ]

    def check(status, headers=None, snapshot=None):
        for target in targets:
            response = client.get(
                url + target, headers=headers, params={"snapshot_id": snapshot} if snapshot else {}
            )
            assert response.status_code == status
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-request-id"]
        response = client.post(
            url + "/groups/query",
            headers=headers,
            json={"conditions": [condition()], **({"snapshot_id": snapshot} if snapshot else {})},
        )
        assert response.status_code == status

    check(409, snapshot="another-snapshot")
    check(200, snapshot=batch.snapshot_id)
    check(401, headers={"Authorization": ""})
    other = {"Authorization": "Bearer " + runtime.other_token}
    check(404, headers=other, snapshot="another-snapshot")
    runtime.admin.grant_read(runtime.other, batch.batch_id)
    check(200, headers=other)
    runtime.admin.revoke_read(runtime.other, batch.batch_id)
    check(404, headers=other)
    runtime.admin.grant_read(runtime.other, batch.batch_id)
    runtime.admin.remove_member(runtime.other, runtime.scope)
    check(404, headers=other)
    runtime.admin.revoke_tokens(runtime.teacher)
    check(401)
    # A cached principal must also fail within the read transaction.
    with pytest.raises(APIError) as exc:
        AnalysisReader(runtime.db).statistics(runtime.p, batch.batch_id)
    assert exc.value.status_code == 401


def test_revision_cannot_substitute_evidence_or_snapshot(client, cohort, runtime, book, stage):
    url, old = cohort
    book["Sheet2"]["C3"] = 1.01
    new_id = commit(runtime, stage(revision=old.batch_id)).batch_id
    new = runtime.service.get_batch(runtime.p, new_id)
    new_url = f"/api/v1/edu/batches/{new_id}"
    evidence_id = client.get(new_url + "/students").json()["items"][0]["scores"][0]["evidence_id"]
    assert client.get(url + "/evidence/" + evidence_id).status_code == 404
    assert (
        client.get(url + "/statistics", params={"snapshot_id": new.snapshot_id}).status_code == 409
    )
    assert client.get(url + "/statistics").json()["dimensions"][0]["minimum"]["value"] == 1
    assert client.get(new_url + "/statistics").json()["dimensions"][0]["minimum"]["value"] == 1.01
    assert client.get(url + "/students/missing/evidence").status_code == 404
    assert client.get(url + "/evidence/missing").status_code == 404


def test_openapi_publishes_numeric_closed_query_and_new_routes(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    prefix = "/api/v1/edu/batches/{batch_id}"
    query = paths[prefix + "/groups/query"]["post"]["requestBody"]["content"]
    model = query["application/json"]["schema"]
    assert model["additionalProperties"] is False
    conditions = model["properties"]["conditions"]
    assert (conditions["minItems"], conditions["maxItems"]) == (1, 2)
    assert conditions["items"]["properties"]["value"]["type"] == "number"
    assert "security" in paths[prefix + "/groups/query"]["post"]
    for suffix in ["/statistics", "/rubric", "/evidence/{evidence_id}"]:
        assert "get" in paths[prefix + suffix]


def test_stat_fraction_ties_and_null():
    assert stat(Fraction(2675, 1000)).display == "2.68"
    assert stat(Fraction(1, 3)).exact.denominator == 3
    assert stat(None).value is None


def test_corrupt_confirmed_facts_fail_instead_of_changing_denominator(client, cohort, runtime):
    url, batch = cohort
    # Simulate an externally damaged database using a private disposable test connection.
    with runtime.db.transaction(write=True) as connection:
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='evaluations'"
        ).fetchall()
        for row in triggers:
            connection.execute('DROP TRIGGER "' + row[0].replace('"', '""') + '"')
        connection.execute(
            "DELETE FROM evaluations WHERE evidence_id=(SELECT evidence_id FROM evaluations "
            "WHERE batch_id=? LIMIT 1)",
            (batch.batch_id,),
        )
    assert client.get(url + "/statistics").status_code == 503
    response = client.post(url + "/groups/query", json={"conditions": [condition()]})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"


def test_all_five_dimension_mappings(client, cohort):
    url, _ = cohort
    for code, expected in [
        (A, 1),
        (B, 2),
        ("problem_modeling", 4),
        ("efficiency_awareness", 2),
        ("solution_strategy", 0),
    ]:
        result = client.post(
            url + "/groups/query", json={"conditions": [condition(code, "lt", 2.75)]}
        ).json()
        assert result["matched_count"] == expected


def test_group_model_preserves_exact_decimal():
    # Python service callers receive the same validated two-decimal policy.
    query = GroupQuery.model_validate({"conditions": [condition()]})
    assert Fraction(query.conditions[0].value) == Fraction(11, 4)
