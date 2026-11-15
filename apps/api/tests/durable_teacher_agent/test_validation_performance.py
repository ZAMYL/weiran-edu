"""Cold-run measurements for the generic durable verification pipeline."""

import json
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from io import BytesIO
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.capability_profiles.engine import ProfileBook
from prototypes.durable_teacher_agent.api import install
from prototypes.durable_teacher_agent.service import TaskService
from prototypes.research_agent_v2.runtime import ResearchExecutor
from prototypes.teacher_agent_bridge.fixtures import build_host
from prototypes.teacher_agent_bridge.provider import DatabaseProvider
from scripts.edu_demo_fixtures import make_fixture

from app.ai.edu.discovery.models import AgentError, Limits
from app.importing.edu import parse_excel
from app.importing.edu.types import DIMENSIONS

from .test_database_profiles import profile_host, start
from .test_tasks import actor


def test_fact_ledger_reuse_invalidates_on_new_fact_and_mutation():
    executor = ResearchExecutor(admit("five"), "validation-cache", Limits())
    first = executor.execute(
        "get_dimension_statistics", executor.parse("get_dimension_statistics", {})
    )[0]
    executor._verify(first)
    initial_replays = executor.replay_executions
    executor._verify(first)
    assert executor.replay_executions == initial_replays

    second = executor.execute(
        "inspect_item_quality",
        executor.parse("inspect_item_quality", {"item_id": "I01"}),
    )[0]
    executor._verify(second)
    assert executor.replay_executions == initial_replays + 2

    saved = first.view["qualified"]
    first.view["qualified"] = not saved
    try:
        with pytest.raises(AgentError, match="source_validation_failed"):
            executor._verify(first)
    finally:
        first.view["qualified"] = saved


def matrix_workbook(rows, seed):
    original = make_fixture(len(rows), seed)
    book = load_workbook(BytesIO(original.data))
    for student_index, values in enumerate(rows):
        for dimension_index, value in enumerate(values):
            book["Sheet2"].cell(3 + student_index * 6 + dimension_index, 3).value = value
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def variable_host(directory, cases):
    fixture = build_host(directory, install_bridge=False)
    fixture.books.update(
        {name: matrix_workbook(rows, 900 + index) for index, (name, rows) in enumerate(cases)}
    )
    fixture.app.state.edu_service.parser = parse_excel
    provider = DatabaseProvider(fixture.app.state.edu_service, tuple(fixture.books.values()))
    return fixture, install(fixture.app, provider, directory / "campaigns")


def rational(value):
    return Fraction(value["numerator"], value["denominator"])


def matches(row, conditions):
    values = {
        dimension.code: Fraction(value) for dimension, value in zip(DIMENSIONS, row, strict=True)
    }
    operations = {
        "<": lambda left, right: left < right,
        "<=": lambda left, right: left <= right,
        "==": lambda left, right: left == right,
        ">=": lambda left, right: left >= right,
        ">": lambda left, right: left > right,
    }
    return all(
        operations[condition["operator"]](
            values[condition["dimension_code"]], Fraction(condition["value"])
        )
        for condition in conditions
    )


def assert_profile_matches_matrix(profile, rows):
    target_indexes = {
        index for index, row in enumerate(rows) if matches(row, profile["conditions"])
    }
    target = [row for index, row in enumerate(rows) if index in target_indexes]
    remainder = [row for index, row in enumerate(rows) if index not in target_indexes]
    assert profile["metrics"]["target_count"] == len(target)
    assert profile["metrics"]["remainder_count"] == len(remainder)
    for dimension in profile["dimensions"]:
        index = next(
            i for i, spec in enumerate(DIMENSIONS) if spec.code == dimension["dimension_code"]
        )
        for side, selected in (("target", target), ("remainder", remainder)):
            values = [Fraction(row[index]) for row in selected]
            assert dimension[side]["valid_count"] == len(values)
            assert rational(dimension[side]["mean"]) == sum(values) / len(values)
            if dimension[side]["distribution_state"] == "available":
                expected = Counter(values)
                actual = {
                    rational(bucket["score"]): bucket["count"]
                    for bucket in dimension[side]["distribution"]
                }
                assert actual == expected
                assert all(count >= profile["minimum_cell"] for count in actual.values())
            else:
                assert dimension[side]["distribution"] == []


def test_profile_pipeline_call_counts(tmp_path, monkeypatch):
    counts = Counter()
    originals = {
        "capture": DatabaseProvider.capture,
        "book_verify": ProfileBook._verify,
        "fact_verify": ResearchExecutor._verify,
        "witness_replay": DatabaseProvider.replay_witnesses,
        "verified_result": TaskService.verified_result,
    }

    def capture(self, actor, batch_id, snapshot):
        counts["capture"] += 1
        material, manifest = originals["capture"](self, actor, batch_id, snapshot)
        counts["captured_participants"] += len(material.data.participants)
        counts["captured_witnesses"] += len(material.witnesses)
        return material, manifest

    def book_verify(self, profile_id):
        counts["profile_candidate_verify_calls"] += 1
        before = self.audit.get("projection_source_replays", 0)
        value = originals["book_verify"](self, profile_id)
        counts["profile_candidate_replays"] += (
            self.audit.get("projection_source_replays", 0) - before
        )
        return value

    def fact_verify(self, fact):
        counts["fact_verify_calls"] += 1
        before = self.replay_executions
        value = originals["fact_verify"](self, fact)
        counts["ledger_query_replays"] += self.replay_executions - before
        return value

    def replay_witnesses(self, source, references):
        counts["witness_replay_calls"] += 1
        counts["witness_references"] += len(references)
        return originals["witness_replay"](self, source, references)

    def verified_result(self, actor, row, *args, **kwargs):
        counts["verified_result_calls"] += 1
        return originals["verified_result"](self, actor, row, *args, **kwargs)

    monkeypatch.setattr(DatabaseProvider, "capture", capture)
    monkeypatch.setattr(ProfileBook, "_verify", book_verify)
    monkeypatch.setattr(ResearchExecutor, "_verify", fact_verify)
    monkeypatch.setattr(DatabaseProvider, "replay_witnesses", replay_witnesses)
    monkeypatch.setattr(TaskService, "verified_result", verified_result)

    fixture, service = profile_host(tmp_path)
    started = perf_counter()
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        _, _, _, _, view = start(client, fixture, service, "balanced72")
    elapsed = perf_counter() - started

    assert view["state"] == "completed"
    evidence = {"elapsed_seconds": elapsed, **dict(sorted(counts.items()))}
    print("VALIDATION_PERFORMANCE_EVIDENCE=" + json.dumps(evidence, sort_keys=True))
    assert counts["capture"] <= 25
    assert counts["ledger_query_replays"] <= 10
    assert counts["profile_candidate_replays"] <= 6
    assert counts["witness_replay_calls"] == 2


def test_varied_formal_workbooks_are_isolated_and_match_database_scores(tmp_path):
    small = tuple(
        (Decimal("1.25"), Decimal("4.50"), Decimal("1") + index % 2, Decimal("4"), Decimal("2"))
        for index in range(6)
    ) + tuple(
        (Decimal("4.75"), Decimal("2"), Decimal("4") + index % 2, Decimal("2"), Decimal("4"))
        for index in range(6)
    )
    medium = tuple(
        (Decimal("1.50"), Decimal("4.25"), Decimal("1.75"), Decimal("4"), Decimal("2.25"))
        for _ in range(9)
    ) + tuple(
        (
            Decimal("4.25"),
            Decimal("2.25"),
            Decimal("3.25") + Decimal(index % 3) / 4,
            Decimal("2.50"),
            Decimal("4.25"),
        )
        for index in range(28)
    )
    large = tuple(
        (Decimal("1.75"), Decimal("4.50"), Decimal("2"), Decimal("4.25"), Decimal("2"))
        for _ in range(25)
    ) + tuple(
        (
            Decimal("4.50"),
            Decimal("2"),
            Decimal("3.25") + Decimal(index % 4) / 4,
            Decimal("2.25"),
            Decimal("4.50"),
        )
        for index in range(74)
    )
    constant = tuple((Decimal("3"),) * 5 for _ in range(29))
    cases = (
        ("near-threshold", small),
        ("unbalanced-medium", medium),
        ("large-varied", large),
        ("no-candidate", constant),
    )
    fixture, service = variable_host(tmp_path, cases)
    signatures = {}
    timings = []
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        for case_index, (name, rows) in enumerate(cases):
            started = perf_counter()
            batch, _, _, _, view = start(
                client,
                fixture,
                service,
                name,
                idempotency_key=f"profile-variable-{case_index:02}",
            )
            timings.append((name, perf_counter() - started))
            assert view["state"] == "completed"
            assert view["result"]["batch_record_count"] == len(rows)
            profiles = view["result"]["profile_projection"]["profiles"]
            if name in {"near-threshold", "no-candidate"}:
                assert profiles == []
                assert view["result"]["stop_reason"] == "no_supported_findings"
                assert view["result"]["profile_projection"]["stop_reason"] == "no_eligible_profiles"
            else:
                assert profiles
            for profile in profiles:
                assert_profile_matches_matrix(profile, rows)

            material, _ = service.provider.capture(
                actor(fixture, service), batch["batch_id"], batch["snapshot_id"]
            )
            with service.store.database.transaction() as connection:
                actual_rows = connection.execute(
                    "SELECT s.public_id,e.dimension_code,e.score_units "
                    "FROM evaluations e JOIN students s USING(student_pk) WHERE e.batch_id=?",
                    (batch["batch_id"],),
                ).fetchall()
            actual = {
                (row["public_id"], row["dimension_code"]): row["score_units"] for row in actual_rows
            }
            for participant, expected in zip(material.data.participants, rows, strict=True):
                for dimension, score in zip(DIMENSIONS, expected, strict=True):
                    assert actual[(participant.participant_id, dimension.code)] == int(score * 100)

            signatures[name] = json.dumps(
                [
                    {
                        "conditions": profile["conditions"],
                        "metrics": profile["metrics"],
                    }
                    for profile in profiles
                ],
                sort_keys=True,
            )
    nonempty_signatures = {
        signature
        for name, signature in signatures.items()
        if name not in {"near-threshold", "no-candidate"}
    }
    assert len(nonempty_signatures) > 1
    print(
        "VARIED_PROFILE_PERFORMANCE_EVIDENCE="
        + json.dumps(
            {
                "cases": [{"name": name, "participants": len(rows)} for name, rows in cases],
                "timings_seconds": dict(timings),
                "slowest_seconds": max(value for _, value in timings),
            },
            sort_keys=True,
        )
    )


def test_validation_scope_fails_closed_if_source_changes_mid_phase(tmp_path, monkeypatch):
    fixture, service = profile_host(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch, _, path, _, view = start(client, fixture, service, "balanced72")
        assert view["state"] == "completed"
        with service.store.database.transaction(write=True) as connection:
            trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='immutable_evaluations_update'"
            ).fetchone()[0]
            original = connection.execute(
                "SELECT evidence_id,score_units FROM evaluations WHERE batch_id=? LIMIT 1",
                (batch["batch_id"],),
            ).fetchone()

        capture = service.provider.capture
        calls = 0

        def change_after_phase_entry(*args):
            nonlocal calls
            calls += 1
            captured = capture(*args)
            if calls == 1:
                with service.store.database.transaction(write=True) as connection:
                    connection.execute("DROP TRIGGER immutable_evaluations_update")
                    connection.execute(
                        "UPDATE evaluations SET score_units=score_units+1 WHERE evidence_id=?",
                        (original["evidence_id"],),
                    )
            return captured

        monkeypatch.setattr(service.provider, "capture", change_after_phase_entry)
        try:
            response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
            assert response.status_code == 503, response.text
            assert calls >= 2
        finally:
            monkeypatch.setattr(service.provider, "capture", capture)
            with service.store.database.transaction(write=True) as connection:
                connection.execute(
                    "UPDATE evaluations SET score_units=? WHERE evidence_id=?",
                    (original["score_units"], original["evidence_id"]),
                )
                connection.execute(trigger)
