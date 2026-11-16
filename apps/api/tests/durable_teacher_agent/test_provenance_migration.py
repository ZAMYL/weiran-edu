"""Existing demo databases gain provenance_json without rewriting any row."""

from types import SimpleNamespace

from prototypes.durable_teacher_agent.store import TaskStore

from app.storage.database import Database


def _store(database):
    provider = SimpleNamespace(service=SimpleNamespace(database=database, clock=lambda: 0.0))
    return TaskStore(provider)


def _columns(database):
    with database.transaction() as connection:
        return {
            row["name"]
            for row in connection.execute("PRAGMA table_info(ai_synthetic_tasks)")
        }


def test_initialize_adds_provenance_json_to_an_existing_task_table(tmp_path):
    database = Database(tmp_path / "old-demo.sqlite3")
    database.migrate()
    with database.transaction(write=True) as connection:
        connection.execute("CREATE TABLE ai_synthetic_tasks (task_id TEXT PRIMARY KEY) STRICT")
        connection.execute(
            "INSERT INTO ai_synthetic_tasks (task_id) VALUES ('old-task')"
        )
    assert "provenance_json" not in _columns(database)
    _store(database).initialize()
    assert "provenance_json" in _columns(database)
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT provenance_json FROM ai_synthetic_tasks WHERE task_id='old-task'"
        ).fetchone()
    assert row["provenance_json"] == "{}"


def test_initialize_is_idempotent_for_a_new_database(tmp_path):
    database = Database(tmp_path / "new-demo.sqlite3")
    database.migrate()
    store = _store(database)
    store.initialize()
    store.initialize()
    assert "provenance_json" in _columns(database)
