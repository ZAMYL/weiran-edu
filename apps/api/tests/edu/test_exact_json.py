"""Real formal responses preserve precision without changing storage or computation."""

import hashlib
import json
import re
from fractions import Fraction

import pytest

from app.analysis.edu.discovery.presentation import exact
from app.core.errors import APIError
from app.core.exact_json import MAX_SAFE_INTEGER as SAFE
from app.core.exact_json import ExactJSONResponse, exact_json_bytes, exact_wire
from app.edu import discovery_bridge, discovery_worker
from app.edu.discovery_models import DiscoveryExact
from app.edu.review_models import AnalysisSnapshot

from .test_discovery_results import exported, save
from .test_insights_api import formal_batch as formal_batch


@pytest.mark.parametrize("number", [0, SAFE, -SAFE, SAFE + 1, -SAFE - 1, 10**40 + 3])
def test_exact_output_changes_representation_only(number):
    value = exact(Fraction(number, SAFE + 1))
    value.update(numerator=number, denominator=SAFE + 1)
    wire = json.loads(ExactJSONResponse(value).body)
    assert int(wire["numerator"]) == number
    assert type(wire["numerator"]) is (int if abs(number) <= SAFE else str)
    assert wire["denominator"] == str(SAFE + 1)
    assert wire["display"] == value["display"] and value["numerator"] == number
    assert wire == exact_wire(value)


@pytest.mark.parametrize("number", [True, "1", 1.5, None])
def test_output_codec_refuses_invalid_internal_exact_components(number):
    with pytest.raises(ValueError, match="INVALID_EXACT_RESPONSE"):
        exact_json_bytes({"numerator": number, "denominator": 1})


def test_counts_notes_and_internal_dumps_remain_unchanged():
    model = DiscoveryExact(numerator=SAFE + 2, denominator=SAFE + 1, display="1.0000")
    before = model.model_dump_json()
    wire = ExactJSONResponse(model.model_dump(mode="json")).body
    assert isinstance(json.loads(before)["numerator"], int)
    assert isinstance(json.loads(wire)["numerator"], str)
    assert model.model_dump_json() == before
    counts = {"student_denominator": 53, "denominator": 53, "count": 53, "note": "Fraction(1, 3)"}
    assert exact_wire(counts) == counts


def test_discovery_budget_checks_the_actual_encoded_bytes(monkeypatch):
    model = DiscoveryExact(numerator=SAFE + 2, denominator=SAFE + 1, display="1.0000")
    monkeypatch.setattr(
        discovery_bridge, "MAX_RESPONSE_BYTES", len(model.model_dump_json().encode())
    )
    assert (
        len(exact_json_bytes(model.model_dump(mode="json"))) > discovery_bridge.MAX_RESPONSE_BYTES
    )
    with pytest.raises(APIError) as failure:
        discovery_bridge.DiscoveryInputReader.bounded(model)
    assert failure.value.status_code == 413
    assert failure.value.code == "DISCOVERY_RESPONSE_TOO_LARGE"


def test_actual_openapi_describes_lossless_exact_response_types(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    exact = schemas["DiscoveryExact"]["properties"]
    for key in ("numerator", "denominator"):
        schema = exact[key]
        if "$ref" in schema:
            schema = schemas[schema["$ref"].split("/")[-1]]
        assert {s["type"] for s in schema["anyOf"]} == {"integer", "string"}
        integer, text = schema["anyOf"]
        assert integer["maximum"] == SAFE
        assert re.fullmatch(text["pattern"], str(SAFE + 1))
        assert not re.fullmatch(text["pattern"], str(SAFE))
        assert not re.fullmatch(text["pattern"], "0" + str(SAFE + 1))


def test_real_relation_save_review_read_and_exports_preserve_original_fraction(
    client, runtime, formal_batch, monkeypatch
):
    base, batch, _ = formal_batch(53)
    execute = discovery_worker.execute
    computed = []

    async def capture(*args, **kwargs):
        result = await execute(*args, **kwargs)
        computed.append(result)
        return result

    monkeypatch.setattr(discovery_worker, "execute", capture)
    spec = {"snapshot_id": batch["snapshot_id"]}
    listed_response = client.post(base + "/discoveries/query", json=spec | {"limit": 50})
    assert listed_response.status_code == 200
    listed = listed_response.json()
    assert [f.discovery_id for f in computed[-1].findings] == [
        f["discovery_id"] for f in listed["items"]
    ]
    card = next(f for f in listed["items"] if f["type"] == "dimension_relation")
    original = next(f for f in computed[-1].findings if f.discovery_id == card["discovery_id"])
    response = client.post(base + "/discoveries/" + card["discovery_id"] + "/query", json=spec)
    assert response.status_code == 200
    detail = response.json()
    score = detail["ranking"]["score"]
    assert isinstance(score["numerator"], str) or isinstance(score["denominator"], str)
    assert Fraction(int(score["numerator"]), int(score["denominator"])) == original.ranking.score
    assert detail["ranking"]["is_statistical_confidence"] is False
    assert detail["scope"]["scope_record_count"] == 53
    chart = client.post(base + "/discoveries/" + card["discovery_id"] + "/chart/query", json=spec)
    assert chart.status_code == 200 and chart.json()["chart"] == detail["chart"]
    created = save(
        client,
        base,
        batch,
        listed,
        discovery={
            "input_key": listed["scope"]["input_key"],
            "discovery_ids": [card["discovery_id"]],
        },
    )
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["analysis"]["discovery"]["findings"][0] == detail
    assert saved["discovery_findings"][0]["discovery"] == detail
    result_path = base + "/analysis-results/" + saved["result_id"]
    assert client.get(result_path).json() == saved
    with runtime.db.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM analysis_results WHERE result_id=?", (saved["result_id"],)
        ).fetchone()
        payload = row["payload_json"]
        assert hashlib.sha256(payload.encode()).hexdigest() == row["payload_sha256"]
    internal = AnalysisSnapshot.model_validate_json(payload)
    assert internal.model_dump_json() == payload
    assert (
        internal.discovery.findings[0].ranking.score.numerator == original.ranking.score.numerator
    )
    assert isinstance(
        json.loads(payload)["discovery"]["findings"][0]["ranking"]["score"]["numerator"], int
    )
    changed = client.patch(
        result_path,
        json=spec
        | {
            "expected_version": 1,
            "decisions": [{"candidate_id": card["discovery_id"], "status": "confirmed"}],
        },
    )
    assert changed.status_code == 200
    reviewed = changed.json()
    assert reviewed["version"] == 2 and reviewed["analysis"] == saved["analysis"]
    for format in ("md", "xlsx", "json", "zip"):
        report = exported(client, base, batch, reviewed, format)
        assert report.status_code == 200
        if format == "json":
            private = report.json()["confirmed_discoveries"][0]["discovery"]["ranking"]["score"]
            assert type(private["numerator"]) is int
            assert private["numerator"] == original.ranking.score.numerator
    with runtime.db.transaction() as connection:
        assert (
            connection.execute(
                "SELECT payload_json FROM analysis_results WHERE result_id=?", (saved["result_id"],)
            ).fetchone()[0]
            == payload
        )
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    assert client.get(result_path).status_code == 200
    assert (
        client.patch(result_path, json=spec | {"expected_version": 2, "note": "x"}).status_code
        == 403
    )
    runtime.admin.revoke_read(runtime.other, batch["batch_id"])
    assert client.get(result_path).status_code == 404
