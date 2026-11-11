"""Real workbook -> formal import -> SQLite -> original task -> verified profiles."""

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from io import BytesIO
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.capability_profiles.database import DatabaseProfileSource
from prototypes.capability_profiles.engine import build_profiles, dataset_from_source
from prototypes.capability_profiles.synthetic import specification
from prototypes.capability_profiles.synthetic import workbook as profile_workbook
from prototypes.durable_teacher_agent.api import install
from prototypes.durable_teacher_agent.models import (
    ProfileInvestigationResult,
    ProfileScoreRequest,
)
from prototypes.durable_teacher_agent.profiles import stable_projection
from prototypes.durable_teacher_agent.service import TaskService
from prototypes.durable_teacher_agent.store import TaskStore, encode
from prototypes.teacher_agent_bridge.fixtures import build_host
from prototypes.teacher_agent_bridge.provider import DatabaseProvider, digest
from scripts.edu_demo_fixtures import make_fixture

from app.ai.edu.discovery.models import AgentError
from app.ai.edu.qwen_config import QwenSettings
from app.core.errors import APIError
from app.importing.edu import parse_excel
from app.importing.edu.types import DIMENSIONS

from .test_tasks import actor, upload


def profile_host(directory):
    fixture = build_host(directory, install_bridge=False)
    fixture.books.update(
        {
            "balanced72": profile_workbook("profile-balanced-72"),
            "zero81": profile_workbook("profile-no-pattern-81"),
            "overlap96": profile_workbook("profile-overlap-96"),
        }
    )
    fixture.app.state.edu_service.parser = parse_excel
    provider = DatabaseProvider(fixture.app.state.edu_service, tuple(fixture.books.values()))
    return fixture, install(fixture.app, provider, directory / "campaigns")


def start(
    client,
    fixture,
    service,
    case,
    question="有哪些值得关注的问题？",
    idempotency_key="profile-once-01",
):
    batch = upload(client, fixture, case)
    service.store.grant(actor(fixture, service), batch["batch_id"], batch["snapshot_id"])
    base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
    body = {
        "snapshot_id": batch["snapshot_id"],
        "question": question,
        "idempotency_key": idempotency_key,
    }
    execution_seconds = []
    original_execute = service.execute

    def timed_execute(task_id):
        started = perf_counter()
        try:
            return original_execute(task_id)
        finally:
            execution_seconds.append(perf_counter() - started)

    service.execute = timed_execute
    post_started = perf_counter()
    try:
        response = client.post(base, json=body)
    finally:
        post_seconds = perf_counter() - post_started
        service.execute = original_execute
    assert response.status_code == 202, response.text
    path = base + "/" + response.json()["task_id"]
    replay_seconds = []
    original_verified = service.verified_result

    def timed_verified(*args, **kwargs):
        started = perf_counter()
        try:
            return original_verified(*args, **kwargs)
        finally:
            replay_seconds.append(perf_counter() - started)

    service.verified_result = timed_verified
    get_started = perf_counter()
    try:
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
    finally:
        get_seconds = perf_counter() - get_started
        service.verified_result = original_verified
    assert response.status_code == 200, response.text
    view = response.json()
    print(
        "DATABASE_PROFILE_EVIDENCE="
        + json.dumps(
            {
                "case": case,
                "http": response.status_code,
                "state": view["state"],
                "error": view["error_code"],
                "post_seconds": post_seconds,
                "task_execution_seconds": execution_seconds[0],
                "get_seconds": get_seconds,
                "proof_replay_seconds": replay_seconds[-1],
                "model_requests": view["model_requests"],
                "tool_attempts": view["tool_attempts"],
                "projection": None
                if view["result"] is None
                else {
                    "profiles": len(view["result"]["profile_projection"]["profiles"]),
                    "stop_reason": view["result"]["profile_projection"]["stop_reason"],
                    "uninvestigated_candidates": view["result"]["profile_projection"][
                        "uninvestigated_candidates"
                    ],
                },
            },
            ensure_ascii=False,
        )
    )
    return batch, base, path, body, view


def profile_scores_path(batch, view):
    return (
        f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks/"
        f"{view['task_id']}/profiles/scores/query"
    )


def profile_scores_body(batch, profile, dimension, side, **page):
    return {
        "snapshot_id": batch["snapshot_id"],
        "profile_id": profile["profile_id"],
        "dimension_code": dimension["dimension_code"],
        "side": side,
        **page,
    }


def no_live_settings():
    raise AssertionError("real_model_settings_must_not_be_read")


@pytest.fixture(autouse=True)
def forbid_live_model(monkeypatch):
    monkeypatch.setattr(QwenSettings, "from_env", no_live_settings)


@pytest.fixture(scope="module")
def completed72(tmp_path_factory):
    directory = tmp_path_factory.mktemp("database-profile72")
    fixture, service = profile_host(directory)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(QwenSettings, "from_env", no_live_settings)
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            values = start(client, fixture, service, "balanced72")
            yield fixture, service, client, values
    fixture.admin.revoke_tokens(fixture.teacher_id)


@pytest.fixture(scope="module")
def completed81(tmp_path_factory):
    directory = tmp_path_factory.mktemp("database-profile81")
    fixture, service = profile_host(directory)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(QwenSettings, "from_env", no_live_settings)
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            values = start(client, fixture, service, "zero81")
            yield fixture, service, client, values
    fixture.admin.revoke_tokens(fixture.teacher_id)


def test_72_http_profiles_are_real_database_statistics_and_witnesses(completed72):
    fixture, service, client, (batch, _, _, _, view) = completed72
    assert view["state"] == "completed", view
    projection = view["result"]["profile_projection"]
    assert projection["assessment_snapshot_id"] == batch["snapshot_id"]
    assert projection["source_manifest_sha256"] != batch["snapshot_id"]
    assert projection["profiles"], projection
    assert view["model_requests"] <= 6 and view["tool_attempts"] <= 5
    with service.store.database.transaction() as connection:
        rows = connection.execute(
            "SELECT s.public_id,e.dimension_code,e.score_units,e.evidence_id "
            "FROM evaluations e JOIN students s USING(student_pk) WHERE e.batch_id=?",
            (batch["batch_id"],),
        ).fetchall()
    scores = {}
    evidence = {}
    for row in rows:
        scores.setdefault(row["public_id"], {})[row["dimension_code"]] = Fraction(
            row["score_units"], 100
        )
        evidence[(row["public_id"], row["dimension_code"])] = row["evidence_id"]
    assert len(scores) == 72 and len(evidence) == 360
    # Compare all 360 actual DB values against the registered workbook's rule matrix.
    matrix = specification("profile-balanced-72")["rows"]
    source_data, _ = service.provider.capture(
        actor(fixture, service), batch["batch_id"], batch["snapshot_id"]
    )
    for participant, original in zip(source_data.data.participants, matrix, strict=True):
        assert [scores[participant.participant_id][d.code] for d in DIMENSIONS] == [
            Fraction(v) for v in original
        ]
    task = service.store.get(actor(fixture, service), view["task_id"])
    source, executor = service.replay(actor(fixture, service), task)
    assert source.fingerprint == projection["source_manifest_sha256"]
    observed_means = set()
    for profile in projection["profiles"]:
        assert profile["batch_id"] == batch["batch_id"]
        assert profile["snapshot_id"] == batch["snapshot_id"]
        assert profile["analysis_snapshot_id"] == source.fingerprint
        assert "source_queries" not in profile
        assert profile["dimension_evidence_http"] == "synthetic_task_profile_scores_v1"
        target = set()
        for pid, values in scores.items():
            if all(
                {
                    "<": values[c["dimension_code"]] < Fraction(c["value"]),
                    "<=": values[c["dimension_code"]] <= Fraction(c["value"]),
                    "==": values[c["dimension_code"]] == Fraction(c["value"]),
                    ">=": values[c["dimension_code"]] >= Fraction(c["value"]),
                    ">": values[c["dimension_code"]] > Fraction(c["value"]),
                }[c["operator"]]
                for c in profile["conditions"]
            ):
                target.add(pid)
        remainder = set(scores) - target
        observed_means.add(
            tuple(sum(scores[pid][d.code] for pid in target) / len(target) for d in DIMENSIONS)
        )
        assert profile["metrics"]["target_count"] == len(target)
        assert profile["metrics"]["remainder_count"] == len(remainder)
        for dim in profile["dimensions"]:
            code = dim["dimension_code"]
            for side, members in (("target", target), ("remainder", remainder)):
                expected = sum(scores[pid][code] for pid in members) / len(members)
                actual = dim[side]["mean"]
                assert Fraction(actual["numerator"], actual["denominator"]) == expected
                assert dim[side]["valid_count"] == len(members)
                counts = {}
                for pid in members:
                    counts[scores[pid][code]] = counts.get(scores[pid][code], 0) + 1
                assert {
                    Fraction(b["score"]["numerator"], b["score"]["denominator"]): b["count"]
                    for b in dim[side]["distribution"]
                } == counts
        for fid in profile["verified_fact_ids"]:
            fact = executor.facts[fid]
            witnesses = executor.resolve(fid, "target.valid")
            assert {w.participant_id for w in witnesses} == target
            outcome = fact.calculation.values["outcome"]
            code = DIMENSIONS[int(outcome[1:]) - 1].code
            assert {w.reference_id for w in witnesses} == {evidence[(pid, code)] for pid in target}
            assert all(w.source_sha256 == source.fingerprint for w in witnesses)
    # Expected *input families*, not a fixed program output count or fixed JSON.
    assert {
        (Fraction(4), Fraction(2), Fraction(4), Fraction(3), Fraction(2)),
        (Fraction(2), Fraction(4), Fraction(3), Fraction(3), Fraction(4)),
    } <= observed_means
    main = projection["profiles"][0]["verified_fact_ids"][0]
    fact = next(f for f in view["result"]["facts"] if f["fact_id"] == main)
    link = next(s for s in fact["source_refs"] if s["metric_key"] == "target.valid")
    response = client.post(link["records_path"], json=link["records_query"] | {"limit": 2})
    assert response.status_code == 200, response.text
    assert response.json()["total_count"] == 24
    for reference in response.json()["items"]:
        actual = client.get(
            reference["evidence_path"], params={"snapshot_id": batch["snapshot_id"]}
        )
        assert actual.status_code == 200, actual.text
        assert actual.json()["comment"] is None


def test_profile_five_dimension_scores_match_database_and_do_not_spend_budget(
    completed72, monkeypatch
):
    fixture, service, client, (batch, _, _, _, view) = completed72
    profile = view["result"]["profile_projection"]["profiles"][0]
    path = profile_scores_path(batch, view)
    before = service.store.get(actor(fixture, service), view["task_id"])
    with service.store.database.transaction() as connection:
        rows = connection.execute(
            "SELECT s.public_id,e.dimension_code,e.score_units,e.evidence_id "
            "FROM evaluations e JOIN students s USING(student_pk) WHERE e.batch_id=?",
            (batch["batch_id"],),
        ).fetchall()
    scores = {
        (row["public_id"], row["dimension_code"]): (
            Fraction(row["score_units"], 100),
            row["evidence_id"],
        )
        for row in rows
    }
    calls = {"capture": 0, "witness_batches": 0}
    capture = service.provider.capture
    replay_witnesses = service.provider.replay_witnesses

    def counted_capture(*args):
        calls["capture"] += 1
        return capture(*args)

    def counted_witnesses(*args):
        calls["witness_batches"] += 1
        return replay_witnesses(*args)

    monkeypatch.setattr(service.provider, "capture", counted_capture)
    monkeypatch.setattr(service.provider, "replay_witnesses", counted_witnesses)
    started = perf_counter()
    responses = []
    evidence_sample = None
    for dimension in profile["dimensions"]:
        for side in ("target", "remainder"):
            response = client.post(
                path,
                json=profile_scores_body(batch, profile, dimension, side, limit=100),
            )
            assert response.status_code == 200, response.text
            result = response.json()
            responses.append(result)
            assert result["detail_status"] == "available"
            assert result["limitation_code"] is None
            assert result["total_count"] == dimension[side]["valid_count"]
            assert result["valid_count"] == len(result["items"])
            assert Fraction(result["mean"]["numerator"], result["mean"]["denominator"]) == Fraction(
                dimension[side]["mean"]["numerator"], dimension[side]["mean"]["denominator"]
            )
            assert result["verified_profile_fact_ids"] == profile["verified_fact_ids"]
            actual_scores = []
            for item in result["items"]:
                expected_score, expected_evidence = scores[
                    (item["student_id"], dimension["dimension_code"])
                ]
                assert item["evidence_id"] == expected_evidence
                observed = Fraction(item["score"]["numerator"], item["score"]["denominator"])
                assert observed == expected_score
                actual_scores.append(observed)
            evidence_sample = evidence_sample or (
                result["items"][0],
                scores[(result["items"][0]["student_id"], dimension["dimension_code"])][0],
            )
            assert sum(actual_scores, Fraction()) / len(actual_scores) == Fraction(
                result["mean"]["numerator"], result["mean"]["denominator"]
            )

    item, expected_score = evidence_sample
    evidence = client.get(item["evidence_path"], params={"snapshot_id": batch["snapshot_id"]})
    assert evidence.status_code == 200, evidence.text
    assert Fraction(str(evidence.json()["score"])) == expected_score

    # Five dimensions share one profile selection Fact, but only its outcome dimension
    # is itself an Agent Fact. The other dimensions remain deterministic profile replay.
    assert sum(bool(result["dimension_fact_ids"]) for result in responses) == 2
    first_dimension = profile["dimensions"][0]
    first = client.post(
        path,
        json=profile_scores_body(batch, profile, first_dimension, "target", offset=0, limit=7),
    ).json()
    second = client.post(
        path,
        json=profile_scores_body(batch, profile, first_dimension, "target", offset=7, limit=7),
    ).json()
    assert first["has_more"] and first["total_count"] == second["total_count"]
    assert {item["evidence_id"] for item in first["items"]}.isdisjoint(
        item["evidence_id"] for item in second["items"]
    )
    after = service.store.get(actor(fixture, service), view["task_id"])
    for key in ("budget_json", "tool_attempts", "disclosure_json", "proof_json"):
        assert after[key] == before[key]
    assert calls["capture"] == 3 * 12
    assert calls["witness_batches"] == 2 * 12
    print(
        "PROFILE_SCORE_QUERY_EVIDENCE="
        + json.dumps(
            {
                "requests": len(responses) + 2,
                "elapsed_seconds": perf_counter() - started,
                "capture_calls": calls["capture"],
                "witness_batches": calls["witness_batches"],
                "sample_response": {
                    key: responses[0][key]
                    for key in (
                        "dimension_code",
                        "side",
                        "detail_status",
                        "mean",
                        "valid_count",
                        "total_count",
                    )
                }
                | {
                    "returned_items": len(responses[0]["items"]),
                    "dimension_fact_count": len(responses[0]["dimension_fact_ids"]),
                },
                "model_requests_before_after": [
                    json.loads(before["budget_json"])["request_count"],
                    json.loads(after["budget_json"])["request_count"],
                ],
                "tool_attempts_before_after": [before["tool_attempts"], after["tool_attempts"]],
            },
            ensure_ascii=False,
        )
    )


def test_72_restart_replays_profiles_and_preserves_single_campaign(completed72):
    fixture, service, client, (batch, base, path, body, view) = completed72
    assert view["state"] == "completed", view
    before = service.store.get(actor(fixture, service), view["task_id"])
    restarted = TaskService(service.provider, TaskStore(service.provider), service.directory)
    reread = restarted.view(
        actor(fixture, service), batch["batch_id"], view["task_id"], batch["snapshot_id"]
    )
    assert reread.model_dump(mode="json") == view
    repeated = client.post(base, json=body)
    assert repeated.status_code == 202 and repeated.json()["task_id"] == view["task_id"]
    after = service.store.get(actor(fixture, service), view["task_id"])
    for key in ("budget_json", "disclosure_json", "tool_attempts", "proof_json"):
        assert before[key] == after[key]
    proof = json.loads(after["proof_json"])
    assert proof["released"] == json.loads(after["disclosure_json"])
    assert set(map(tuple, proof["tool_proof"]["released"])) <= set(map(tuple, proof["released"]))
    assert len(list(service.directory.rglob("*.jsonl"))) == 1


def test_legacy_profile_result_remains_readable_but_is_not_auto_upgraded(completed72):
    fixture, service, client, (batch, _, path, _, view) = completed72
    task_id = view["task_id"]
    before = service.store.get(actor(fixture, service), task_id)
    saved = json.loads(before["result_json"])
    proof = json.loads(before["proof_json"])
    projection = saved["profile_projection"]
    projection["dimension_evidence_http"] = "not_implemented"
    for profile in projection["profiles"]:
        profile["dimension_evidence_http"] = "not_implemented"
    model = ProfileInvestigationResult.model_validate_json(encode(saved))
    proof["profile"]["projection_sha"] = digest(stable_projection(model.profile_projection))
    with service.store.database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE ai_synthetic_tasks SET result_json=?,result_sha=?,proof_json=? WHERE task_id=?",
            (encode(saved), digest(saved), encode(proof), task_id),
        )
    try:
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
        assert response.status_code == 200, response.text
        assert (
            response.json()["result"]["profile_projection"]["dimension_evidence_http"]
            == "not_implemented"
        )
        profile = projection["profiles"][0]
        blocked_response = client.post(
            profile_scores_path(batch, view),
            json=profile_scores_body(batch, profile, profile["dimensions"][0], "target"),
        )
        assert blocked_response.status_code == 409, blocked_response.text
        assert blocked_response.json()["error"]["code"] == "AI_PROFILE_EVIDENCE_NOT_ENABLED"
        restarted = TaskService(service.provider, TaskStore(service.provider), service.directory)
        assert (
            restarted.view(
                actor(fixture, service), batch["batch_id"], task_id, batch["snapshot_id"]
            ).result.profile_projection.dimension_evidence_http
            == "not_implemented"
        )
    finally:
        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_tasks SET result_json=?,result_sha=?,proof_json=? "
                "WHERE task_id=?",
                (before["result_json"], before["result_sha"], before["proof_json"], task_id),
            )


def test_81_normal_zero_profiles(completed81):
    _, _, _, (_, _, _, _, view) = completed81
    assert view["state"] == "completed", view
    p = view["result"]["profile_projection"]
    assert p["profiles"] == [] and p["stop_reason"] == "no_eligible_profiles"
    assert p["uninvestigated_candidates"] == 0


def test_profile_score_query_rejects_zero_unknown_dimension_and_forged_fact(
    completed72, completed81
):
    _, _, client72, (batch72, _, _, _, view72) = completed72
    profile = view72["result"]["profile_projection"]["profiles"][0]
    dimension = profile["dimensions"][0]
    path72 = profile_scores_path(batch72, view72)
    unknown = client72.post(
        path72,
        json=profile_scores_body(
            batch72, profile | {"profile_id": "profile-missing"}, dimension, "target"
        ),
    )
    assert unknown.status_code == 404
    invalid_dimension = client72.post(
        path72,
        json=profile_scores_body(batch72, profile, dimension, "target")
        | {"dimension_code": "invented_dimension"},
    )
    assert invalid_dimension.status_code == 422
    forged_fact = client72.post(
        path72,
        json=profile_scores_body(batch72, profile, dimension, "target")
        | {"fact_id": profile["verified_fact_ids"][0]},
    )
    assert forged_fact.status_code == 422

    _, _, client81, (batch81, _, _, _, view81) = completed81
    zero = client81.post(
        profile_scores_path(batch81, view81),
        json={
            "snapshot_id": batch81["snapshot_id"],
            "profile_id": "profile-missing",
            "dimension_code": "algorithm_data_structure",
            "side": "target",
        },
    )
    assert zero.status_code == 404
    assert zero.json()["error"]["code"] == "AI_PROFILE_NOT_FOUND"


def test_81_profile_result_cannot_downgrade_after_persistent_tampering(completed81):
    fixture, service, client, (batch, _, path, _, view) = completed81
    task_id = view["task_id"]
    before = service.store.get(actor(fixture, service), task_id)
    saved = json.loads(before["result_json"])
    proof = json.loads(before["proof_json"])
    assert saved.pop("profile_projection")["profiles"] == []
    saved["batch_record_count"] = 999
    forged = deepcopy(saved["facts"][0]["metrics"][0])
    forged["key"] = "forged.persisted.metric"
    forged["number"] = 999
    forged["exact"] = {"numerator": 999, "denominator": 1}
    saved["facts"][0]["metrics"].append(forged)
    source = saved["facts"][0]["source_refs"][0]

    def persist(result, persisted_proof):
        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_tasks SET result_json=?,result_sha=?,proof_json=? "
                "WHERE task_id=?",
                (encode(result), digest(result), encode(persisted_proof), task_id),
            )

    try:
        persist(saved, proof)
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
        statuses = {"get": response.status_code}
        print(
            "PROFILE_DOWNGRADE_HTTP_EVIDENCE="
            + json.dumps(
                {
                    "get": response.status_code,
                    "batch_record_count": response.json()
                    .get("result", {})
                    .get("batch_record_count"),
                    "forged_metric_returned": any(
                        metric.get("key") == "forged.persisted.metric"
                        for fact in response.json().get("result", {}).get("facts", [])
                        for metric in fact.get("metrics", [])
                    ),
                },
                ensure_ascii=False,
            )
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "AI_PROFILE_PROOF_CONFLICT"

        score_response = client.post(
            profile_scores_path(batch, view),
            json={
                "snapshot_id": batch["snapshot_id"],
                "profile_id": "profile-forged",
                "dimension_code": "algorithm_data_structure",
                "side": "target",
            },
        )
        assert score_response.status_code == 503, score_response.text
        assert score_response.json()["error"]["code"] == "AI_PROFILE_PROOF_CONFLICT"
        statuses["profile_scores"] = score_response.status_code

        restarted = TaskService(service.provider, TaskStore(service.provider), service.directory)
        with pytest.raises(APIError) as error:
            restarted.view(
                actor(fixture, service), batch["batch_id"], task_id, batch["snapshot_id"]
            )
        assert error.value.status_code == 503 and error.value.code == "AI_PROFILE_PROOF_CONFLICT"
        statuses["restart"] = error.value.status_code

        response = client.post(source["records_path"], json=source["records_query"] | {"limit": 1})
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "AI_PROFILE_PROOF_CONFLICT"
        statuses["records_query"] = response.status_code

        stripped = deepcopy(proof)
        stripped.pop("profile")
        stripped.pop("tool_proof")
        persist(saved, stripped)
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "AI_PROFILE_PROOF_CONFLICT"
        statuses["result_and_proof_type_tamper"] = response.status_code

        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_tasks SET proof_json=NULL WHERE task_id=?", (task_id,)
            )
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "AI_PROFILE_PROOF_CONFLICT"
        statuses["missing_proof"] = response.status_code

        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_tasks SET proof_json='{' WHERE task_id=?", (task_id,)
            )
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "AI_PROFILE_PROOF_CONFLICT"
        statuses["malformed_proof"] = response.status_code
        print("PROFILE_INTEGRITY_FIXED_EVIDENCE=" + json.dumps(statuses, ensure_ascii=False))
    finally:
        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_tasks SET result_json=?,result_sha=?,proof_json=? "
                "WHERE task_id=?",
                (before["result_json"], before["result_sha"], before["proof_json"], task_id),
            )


def test_profile_readback_scope_and_actual_source_changes(completed72):
    fixture, service, client, (batch, base, path, body, view) = completed72
    assert view["state"] == "completed"
    profile = view["result"]["profile_projection"]["profiles"][0]
    dimension = profile["dimensions"][0]
    score_path = profile_scores_path(batch, view)
    score_body = profile_scores_body(batch, profile, dimension, "target")
    assert client.get(path, params={"snapshot_id": "old-snapshot"}).status_code == 409
    assert (
        client.post(score_path, json=score_body | {"snapshot_id": "old-snapshot"}).status_code
        == 409
    )
    other_batch = upload(client, fixture, "zero81")
    wrong_path = path.replace(batch["batch_id"], other_batch["batch_id"])
    assert (
        client.get(wrong_path, params={"snapshot_id": other_batch["snapshot_id"]}).status_code
        == 409
    )
    assert (
        client.post(
            score_path.replace(batch["batch_id"], other_batch["batch_id"]),
            json=score_body | {"snapshot_id": other_batch["snapshot_id"]},
        ).status_code
        == 409
    )
    other = fixture.admin.create_teacher("profile-other-teacher")
    token = fixture.admin.issue_token(other)
    try:
        assert (
            client.get(
                path,
                params={"snapshot_id": batch["snapshot_id"]},
                headers={"Authorization": "Bearer " + token},
            ).status_code
            == 404
        )
        assert (
            client.post(
                score_path,
                json=score_body,
                headers={"Authorization": "Bearer " + token},
            ).status_code
            == 404
        )
    finally:
        fixture.admin.revoke_tokens(other)
    with service.store.database.transaction(write=True) as c:
        # Test-only corruption beyond the formal immutable-score guard. Restore it below.
        trigger = c.execute(
            "SELECT sql FROM sqlite_master WHERE name='immutable_evaluations_update'"
        ).fetchone()[0]
        c.execute("DROP TRIGGER immutable_evaluations_update")
        value = c.execute(
            "SELECT evidence_id,score_units FROM evaluations WHERE batch_id=? LIMIT 1",
            (batch["batch_id"],),
        ).fetchone()
        c.execute(
            "UPDATE evaluations SET score_units=score_units+1 WHERE evidence_id=?",
            (value["evidence_id"],),
        )
    try:
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
        assert response.status_code == 503, response.text
        score_response = client.post(score_path, json=score_body)
        assert score_response.status_code == 503, score_response.text
    finally:
        with service.store.database.transaction(write=True) as c:
            c.execute(
                "UPDATE evaluations SET score_units=? WHERE evidence_id=?",
                (value["score_units"], value["evidence_id"]),
            )
            c.execute(trigger)
    assert (
        client.post(base, json=body | {"idempotency_key": "cannot-start-again"}).status_code == 409
    )


@pytest.mark.parametrize("changed", ["profile_mean", "disclosure", "rule", "malformed"])
def test_restart_recomputes_instead_of_trusting_saved_json(completed72, changed):
    fixture, service, client, (batch, _, path, _, view) = completed72
    task_id = view["task_id"]
    profile = view["result"]["profile_projection"]["profiles"][0]
    dimension = profile["dimensions"][0]
    score_path = profile_scores_path(batch, view)
    score_body = profile_scores_body(batch, profile, dimension, "target")
    before = service.store.get(actor(fixture, service), task_id)
    saved = json.loads(before["result_json"])
    proof = json.loads(before["proof_json"])
    disclosure = json.loads(before["disclosure_json"])
    if changed == "profile_mean":
        saved["profile_projection"]["profiles"][0]["dimensions"][0]["target"]["mean"][
            "numerator"
        ] += 1
        # Even a rewritten JSON checksum/projection hash is not a calculation proof.
        model = ProfileInvestigationResult.model_validate_json(encode(saved))
        proof["profile"]["projection_sha"] = digest(stable_projection(model.profile_projection))
    elif changed == "disclosure":
        unique = next(g for g in disclosure if g not in proof["tool_proof"]["released"])
        disclosure.remove(unique)
        proof["released"] = disclosure
    elif changed == "rule":
        proof["profile"]["explanation_version"] = "obsolete-rule"
    else:
        saved["profile_projection"]["profiles"] = "invalid"
    with service.store.database.transaction(write=True) as c:
        c.execute(
            "UPDATE ai_synthetic_tasks SET result_json=?,result_sha=?,proof_json=?,"
            "disclosure_json=? WHERE task_id=?",
            (encode(saved), digest(saved), encode(proof), encode(disclosure), task_id),
        )
    try:
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
        assert response.status_code == 503, response.text
        score_response = client.post(score_path, json=score_body)
        assert score_response.status_code == 503, score_response.text
    finally:
        with service.store.database.transaction(write=True) as c:
            c.execute(
                "UPDATE ai_synthetic_tasks SET result_json=?,result_sha=?,proof_json=?,"
                "disclosure_json=? WHERE task_id=?",
                (
                    *[
                        before[k]
                        for k in ("result_json", "result_sha", "proof_json", "disclosure_json")
                    ],
                    task_id,
                ),
            )


@pytest.mark.parametrize("cap", ["tool_attempts", "model_requests"])
def test_profile_budget_exhaustion_is_not_completed_or_published(tmp_path, cap):
    fixture, service = profile_host(tmp_path)
    service.profile_limits = replace(
        service.profile_limits, **{cap: 2 if cap == "tool_attempts" else 1}
    )
    try:
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            batch, base, _, body, view = start(client, fixture, service, "balanced72")
            assert view["state"] == "failed" and view["result"] is None, view
            assert view["error_code"] == "AI_PROFILE_" + (
                "TOOL_BUDGET_EXHAUSTED" if cap == "tool_attempts" else "MODEL_BUDGET_EXHAUSTED"
            )
            assert view[cap] == (2 if cap == "tool_attempts" else 1)
            before = service.store.get(actor(fixture, service), view["task_id"])
            assert before["result_json"] is None and before["proof_json"] is None
            again = client.post(base, json=body)
            assert again.status_code == 202 and again.json()["task_id"] == view["task_id"]
            assert (
                client.post(base, json=body | {"idempotency_key": "no-budget-reset"}).status_code
                == 409
            )
            after = service.store.get(actor(fixture, service), view["task_id"])
            assert before["budget_json"] == after["budget_json"]
            assert before["disclosure_json"] == after["disclosure_json"]
            assert len(list(service.directory.rglob("*.jsonl"))) == 1
            restarted = TaskService(
                service.provider,
                TaskStore(service.provider),
                service.directory,
                profile_limits=service.profile_limits,
            )
            assert (
                restarted.view(
                    actor(fixture, service),
                    batch["batch_id"],
                    view["task_id"],
                    batch["snapshot_id"],
                ).result
                is None
            )
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)


@pytest.mark.parametrize("action", ["cancel", "timeout", "revoke"])
def test_profile_inflight_guard_keeps_charged_seed_and_no_projection(tmp_path, monkeypatch, action):
    fixture, service = profile_host(tmp_path)
    original = service.store.audit

    def interrupt(task_id, phase, executor, entry):
        original(task_id, phase, executor, entry)
        if phase == "attempt":
            row = service.store.get(actor(fixture, service), task_id)
            if action == "cancel":
                service.store.cancel(actor(fixture, service), task_id)
            elif action == "timeout":
                monkeypatch.setattr(service.store, "clock", lambda: row["deadline"] + 1)
            else:
                service.store.revoke(row["grant_id"])

    monkeypatch.setattr(service.store, "audit", interrupt)
    try:
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            batch = upload(client, fixture, "balanced72")
            service.store.grant(actor(fixture, service), batch["batch_id"], batch["snapshot_id"])
            base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
            body = {
                "snapshot_id": batch["snapshot_id"],
                "question": "有哪些值得关注的问题？",
                "idempotency_key": "inflight-profile-01",
            }
            response = client.post(base, json=body)
            assert response.status_code == 202, response.text
            task_id = response.json()["task_id"]
            with service.store.database.transaction() as c:
                row = service.store._row(c, task_id)
            assert (
                row["state"]
                == {"cancel": "cancelled", "timeout": "timed_out", "revoke": "revoked"}[action]
            )
            assert row["result_json"] is None and row["tool_attempts"] == 1
            assert json.loads(row["budget_json"])["request_count"] == 0
            assert len(list(service.directory.rglob("*.jsonl"))) == 1
            service.execute(task_id)
            with service.store.database.transaction() as c:
                current = service.store._row(c, task_id)
            assert current["budget_json"] == row["budget_json"] and current["tool_attempts"] == 1
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)


def test_profile_k25_cumulative_histogram_suppression_survives_restart(tmp_path):
    fixture, service = profile_host(tmp_path)
    service.profile_limits = replace(service.profile_limits, minimum_cell=25)
    try:
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            batch, base, _, body, view = start(client, fixture, service, "overlap96")
            assert view["state"] == "completed", view
            p = view["result"]["profile_projection"]
            assert p["profiles"] and p["minimum_cell"] == 25
            suppressed = False
            restricted = []
            for profile in p["profiles"]:
                for dimension in profile["dimensions"]:
                    for side in ("target", "remainder"):
                        cells = dimension[side]["distribution"]
                        suppressed |= not cells
                        if not cells:
                            restricted.append((profile, dimension, side))
                        assert all(c["count"] >= 25 for c in cells)
            assert suppressed
            profile, dimension, side = restricted[0]
            score_path = profile_scores_path(batch, view)
            score_body = profile_scores_body(batch, profile, dimension, side)
            before = service.store.get(actor(fixture, service), view["task_id"])
            response = client.post(score_path, json=score_body)
            assert response.status_code == 200, response.text
            limited = response.json()
            assert limited["detail_status"] == "privacy_limited"
            assert limited["limitation_code"] == "PROFILE_SCORE_DETAIL_PRIVACY_LIMITED"
            assert limited["items"] == [] and limited["total_count"] is None
            assert not limited["has_more"]
            # Offset/limit probing cannot turn a suppressed histogram into a count oracle.
            probed = client.post(score_path, json=score_body | {"offset": 999, "limit": 1})
            assert probed.status_code == 200
            assert probed.json()["items"] == [] and probed.json()["total_count"] is None
            after = service.store.get(actor(fixture, service), view["task_id"])
            for key in ("budget_json", "tool_attempts", "disclosure_json", "proof_json"):
                assert after[key] == before[key]
            row = service.store.get(actor(fixture, service), view["task_id"])
            groups = [frozenset(g) for g in json.loads(row["disclosure_json"])]
            universe = set().union(*groups)
            atoms = {}
            for pid in universe:
                key = tuple(pid in g for g in groups)
                atoms[key] = atoms.get(key, 0) + 1
            assert atoms and min(atoms.values()) >= 25
            restarted = TaskService(
                service.provider,
                TaskStore(service.provider),
                service.directory,
                profile_limits=service.profile_limits,
            )
            assert (
                restarted.view(
                    actor(fixture, service),
                    batch["batch_id"],
                    view["task_id"],
                    batch["snapshot_id"],
                ).model_dump(mode="json")
                == view
            )
            assert (
                restarted.profile_scores(
                    actor(fixture, service),
                    batch["batch_id"],
                    view["task_id"],
                    ProfileScoreRequest.model_validate(score_body),
                ).model_dump(mode="json")
                == limited
            )
            assert (
                client.post(base, json=body | {"idempotency_key": "cannot-probe-next"}).status_code
                == 409
            )
            service.store.revoke(row["grant_id"])
            revoked = client.post(score_path, json=score_body)
            assert revoked.status_code == 403
            assert revoked.json()["error"]["code"] == "AI_PURPOSE_REVOKED"
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)


@pytest.mark.parametrize("name", ["contest13", "contest13_complete"])
def test_thirteen_question_protocol_is_not_a_five_dimension_profile(name):
    with pytest.raises(AgentError, match="five_dimension_profile_source_required"):
        build_profiles(admit(name))
    with pytest.raises(AgentError, match="trusted_database_profile_source_required"):
        DatabaseProfileSource(admit(name)).validate()
    with pytest.raises(AgentError, match="registered_synthetic_source_required"):
        dataset_from_source(admit(name).envelope)


def test_unregistered_formal_file_and_invalid_protocol_never_create_ai_task(tmp_path):
    fixture, service = profile_host(tmp_path)
    try:
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            fixture.books["unregistered"] = make_fixture(53, 993).data
            batch = upload(client, fixture, "unregistered")
            with pytest.raises(APIError) as error:
                service.store.grant(
                    actor(fixture, service), batch["batch_id"], batch["snapshot_id"]
                )
            assert error.value.status_code == 403
            for fault in ("missing", "numeric_columns"):
                book = load_workbook(BytesIO(fixture.books["balanced72"]))
                if fault == "missing":
                    book["Sheet2"]["C3"] = None
                else:
                    del book["Sheet1"]
                    sheet = book["Sheet2"]
                    sheet.delete_rows(1, sheet.max_row)
                    for values in specification("profile-balanced-72")["rows"]:
                        sheet.append([Decimal(v) for v in values])
                output = BytesIO()
                book.save(output)
                book.close()
                response = client.post(
                    "/api/v1/edu/imports/preview",
                    files={
                        "file": (
                            "synthetic.xlsx",
                            output.getvalue(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    },
                )
                if response.status_code == 201:
                    preview = response.json()["import"]
                    commit = client.post(
                        f"/api/v1/edu/imports/{preview['import_id']}/commit",
                        json={
                            "preview_version": preview["preview_version"],
                            "acknowledge_warnings": True,
                        },
                    )
                    assert commit.status_code == 409, commit.text
                    assert commit.json()["error"]["code"] == "IMPORT_NOT_CONFIRMABLE"
                else:
                    assert response.status_code == 422, response.text
            with service.store.database.transaction() as c:
                assert c.execute("SELECT count(*) FROM ai_synthetic_tasks").fetchone()[0] == 0
                assert c.execute("SELECT count(*) FROM ai_synthetic_events").fetchone()[0] == 0
            assert not list(service.directory.rglob("*.jsonl"))
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)


def test_profile_get_rechecks_purpose_after_actual_database_capture(tmp_path, monkeypatch):
    fixture, service = profile_host(tmp_path)
    try:
        with TestClient(
            fixture.app, headers={"Authorization": "Bearer " + fixture.token}
        ) as client:
            batch, _, path, _, view = start(client, fixture, service, "zero81")
            assert view["state"] == "completed"
            before = service.store.get(actor(fixture, service), view["task_id"])
            capture = service.provider.capture

            def revoke_after_capture(*args):
                material = capture(*args)
                service.store.revoke(before["grant_id"])
                return material

            monkeypatch.setattr(service.provider, "capture", revoke_after_capture)
            response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "AI_PURPOSE_REVOKED"
            with service.store.database.transaction() as c:
                after = service.store._row(c, view["task_id"])
            assert after["state"] == "revoked" and after["result_json"] is None
            for key in ("budget_json", "disclosure_json", "tool_attempts"):
                assert after[key] == before[key]
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)
