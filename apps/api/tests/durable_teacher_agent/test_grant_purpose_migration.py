"""The grant-purpose migration widens one CHECK without changing Mock meaning."""

import pytest
from fastapi.testclient import TestClient

from app.core.errors import APIError

from .test_tasks import setup, upload

OLD_GRANTS = (
    "CREATE TABLE ai_synthetic_grants_next ("
    "grant_id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL REFERENCES teachers, "
    "batch_id TEXT NOT NULL REFERENCES batches, snapshot_id TEXT NOT NULL, "
    "source_sha TEXT NOT NULL, purpose TEXT NOT NULL CHECK(purpose='synthetic_mock'), "
    "expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, "
    "UNIQUE(teacher_id,batch_id,snapshot_id)) STRICT"
)


def host(tmp_path):
    fixture, service = setup(tmp_path)
    client = TestClient(fixture.app)
    client.headers["Authorization"] = "Bearer " + fixture.token
    batch = upload(client, fixture)
    with service.store.database.transaction() as connection:
        teacher = connection.execute("SELECT teacher_id FROM teachers LIMIT 1").fetchone()[0]
    return service, batch, teacher


def purpose_sql(store):
    with store.database.transaction() as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_synthetic_grants'"
        ).fetchone()
    return row["sql"]


def insert(service, values):
    with service.store.database.transaction(write=True) as connection:
        connection.execute("INSERT INTO ai_synthetic_grants VALUES (?,?,?,?,?,?,?,0)", values)


def test_new_schema_accepts_database_live_and_rejects_unknown_purposes(tmp_path):
    service, batch, teacher = host(tmp_path)
    assert "database_live" in purpose_sql(service.store)
    insert(
        service,
        ("live", teacher, batch["batch_id"], batch["snapshot_id"], "a" * 64, "database_live", 0.0),
    )
    with pytest.raises(APIError):
        insert(
            service,
            ("other", teacher, batch["batch_id"], "other-snapshot", "a" * 64, "unknown", 0.0),
        )


def test_old_schema_is_rebuilt_and_rows_are_preserved(tmp_path):
    service, batch, teacher = host(tmp_path)
    store = service.store
    store.database.rebuild(
        (
            OLD_GRANTS,
            "INSERT INTO ai_synthetic_grants_next SELECT * FROM ai_synthetic_grants",
            "DROP TABLE ai_synthetic_grants",
            "ALTER TABLE ai_synthetic_grants_next RENAME TO ai_synthetic_grants",
        )
    )
    insert(
        service,
        (
            "legacy",
            teacher,
            batch["batch_id"],
            batch["snapshot_id"],
            "a" * 64,
            "synthetic_mock",
            0.0,
        ),
    )
    assert "database_live" not in purpose_sql(store)

    store._allow_database_live_grants()

    assert "database_live" in purpose_sql(store)
    with store.database.transaction() as connection:
        rows = connection.execute(
            "SELECT grant_id, purpose FROM ai_synthetic_grants ORDER BY grant_id"
        ).fetchall()
    assert [(row["grant_id"], row["purpose"]) for row in rows] == [("legacy", "synthetic_mock")]

    store._allow_database_live_grants()  # idempotent
    with store.database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_synthetic_grants").fetchone()[0] == 1
