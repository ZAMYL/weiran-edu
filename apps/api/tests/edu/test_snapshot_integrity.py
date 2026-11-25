"""Synthetic reproduction of immutable snapshot and cross-endpoint consistency gaps."""

import sqlite3

import pytest

from app.core.errors import APIError

from .conftest import commit


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE students SET public_id='changed-synthetic-public-id' WHERE rowid=1",
        "UPDATE rubric_snapshots SET rubric_version='changed-synthetic-version' WHERE rowid=1",
    ],
)
def test_public_identity_and_rubric_version_cannot_change_under_same_snapshot(
    runtime, stage, statement
):
    created = commit(runtime, stage())
    before = runtime.reader.students(runtime.p, created.batch_id)
    with pytest.raises(APIError) as error:
        with runtime.db.transaction(write=True) as connection:
            connection.execute(statement)
    assert error.value.status_code == 503
    assert runtime.reader.students(runtime.p, created.batch_id) == before


def damage_second_student(runtime, batch_id, kind):
    """Simulate external damage only in this fixture's disposable SQLite database."""
    with runtime.db.transaction(write=True) as connection:
        connection.execute("DROP TRIGGER immutable_evaluations_delete")
        connection.execute("DROP TRIGGER immutable_evaluations_update")
        evidence = connection.execute(
            "SELECT e.evidence_id FROM evaluations e JOIN batch_students bs "
            "ON bs.batch_id=e.batch_id AND bs.student_pk=e.student_pk "
            "WHERE e.batch_id=? AND bs.display_order=2 "
            "AND e.dimension_code='algorithm_data_structure'",
            (batch_id,),
        ).fetchone()[0]
        if kind == "missing":
            connection.execute("DELETE FROM evaluations WHERE evidence_id=?", (evidence,))
        elif kind == "source_hash":
            connection.execute(
                "UPDATE evaluations SET file_sha256=? WHERE evidence_id=?", ("0" * 64, evidence)
            )
        elif kind == "source_cell":
            connection.execute(
                "UPDATE evaluations SET score_cell='C999' WHERE evidence_id=?", (evidence,)
            )
        else:
            connection.execute("DROP TRIGGER immutable_rubric_criteria_update")
            connection.execute(
                "UPDATE rubric_criteria SET source_range='L99:P100' WHERE rubric_id="
                "(SELECT rubric_id FROM batches WHERE batch_id=?) "
                "AND dimension_code='algorithm_data_structure'",
                (batch_id,),
            )


@pytest.mark.parametrize("kind", ["missing", "source_hash", "source_cell", "rubric_range"])
def test_damage_outside_requested_page_is_rejected_by_all_formal_fact_reads(
    client, runtime, stage, kind
):
    created = commit(runtime, stage())
    base = f"/api/v1/edu/batches/{created.batch_id}"
    student = client.get(base + "/students?limit=1").json()["items"][0]
    damage_second_student(runtime, created.batch_id, kind)
    paths = (
        "/students?limit=1",
        "/students?offset=99&limit=1",
        "/statistics",
        "/evidence/" + student["scores"][0]["evidence_id"],
        "/students/" + student["student_id"] + "/evidence",
        "/rubric",
    )
    for path in paths:
        response = client.get(base + path)
        assert response.status_code == 503, path
        assert response.json()["error"]["code"] == "DATA_INTEGRITY_ERROR"
    response = client.post(
        base + "/groups/query",
        json={
            "conditions": [
                {"dimension_code": "algorithm_data_structure", "operator": "gte", "value": 1}
            ]
        },
    )
    assert response.status_code == 503


def test_authorization_and_snapshot_checks_precede_corruption_details(client, runtime, stage):
    created = commit(runtime, stage())
    base = f"/api/v1/edu/batches/{created.batch_id}"
    damage_second_student(runtime, created.batch_id, "missing")
    response = client.get(
        base + "/statistics",
        headers={"Authorization": "Bearer " + runtime.other_token},
        params={"snapshot_id": "wrong-snapshot"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    response = client.get(base + "/statistics", params={"snapshot_id": "wrong-snapshot"})
    assert response.status_code == 409
    runtime.admin.revoke_tokens(runtime.teacher)
    assert client.get(base + "/statistics").status_code == 401


def test_fact_validation_does_not_read_private_source_contents(client, runtime, stage, monkeypatch):
    created = commit(runtime, stage())
    base = f"/api/v1/edu/batches/{created.batch_id}"
    student = client.get(base + "/students").json()["items"][0]
    original_connect = runtime.db._connect
    forbidden = {
        "identity_cell_raw",
        "comment_raw",
        "comment_display",
        "student_number",
        "student_name",
        "text_cells_json",
        "raw_xlsx",
    }

    def connect():
        connection = original_connect()

        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and column in forbidden:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(runtime.db, "_connect", connect)
    for path in (
        "/students?limit=1",
        "/students?offset=99",
        "/statistics",
        "/rubric",
        "/evidence/" + student["scores"][0]["evidence_id"],
        "/students/" + student["student_id"] + "/evidence",
    ):
        assert client.get(base + path).status_code == 200, path
    assert (
        client.post(
            base + "/groups/query",
            json={
                "conditions": [
                    {"dimension_code": "algorithm_data_structure", "operator": "gte", "value": 1}
                ]
            },
        ).status_code
        == 200
    )


def test_existing_two_migration_database_upgrades_without_changing_facts(runtime, stage):
    created = commit(runtime, stage())
    before = runtime.reader.students(runtime.p, created.batch_id)
    # Reconstruct the previous schema state in this disposable fixture only.
    with runtime.db.transaction(write=True) as connection:
        connection.execute("DROP TRIGGER immutable_students_update")
        connection.execute("DROP TRIGGER immutable_students_delete")
        connection.execute("DROP TABLE assessment_diagnostics")
        connection.execute("DROP TABLE assessment_materials")
        connection.execute("DROP TABLE assessment_assets")
        connection.execute("DROP TABLE material_confirmations")
        connection.execute("DROP TABLE material_records")
        connection.execute("DROP TABLE analysis_result_versions")
        connection.execute("DROP TABLE analysis_results")
        connection.execute("DROP TABLE text_reviews")
        connection.execute("DROP TABLE teacher_batch_archive")
        connection.execute("DROP TABLE source_assets")
        connection.execute("DROP TABLE problem_dimensions")
        connection.execute("DROP TABLE test_observations")
        connection.execute("DROP TABLE submissions")
        connection.execute("DROP TABLE problems")
        connection.execute("DROP TABLE assessment_participants")
        connection.execute("DROP TABLE ai_programming_events")
        connection.execute("DROP TABLE ai_programming_tasks")
        connection.execute("DROP TABLE ai_programming_grants")
        connection.execute("DROP TABLE assessments")
        connection.execute("DROP INDEX batches_snapshot_reference")
        connection.execute(
            "DELETE FROM schema_migrations WHERE version NOT IN "
            "('001_edu.sql','002_immutable_facts.sql')"
        )
        prior = [
            tuple(row)
            for row in connection.execute(
                "SELECT version,checksum,applied_at FROM schema_migrations ORDER BY version"
            )
        ]
    assert len(prior) == 2
    runtime.db.migrate()
    runtime.db.migrate()
    with runtime.db.transaction() as connection:
        current = [
            tuple(row)
            for row in connection.execute(
                "SELECT version,checksum,applied_at FROM schema_migrations ORDER BY version"
            )
        ]
    assert current[:2] == prior
    assert len(current) == 14
    assert runtime.reader.students(runtime.p, created.batch_id) == before
    with pytest.raises(APIError):
        with runtime.db.transaction(write=True) as connection:
            connection.execute(
                "UPDATE students SET public_id='changed-after-upgrade' WHERE rowid=1"
            )
