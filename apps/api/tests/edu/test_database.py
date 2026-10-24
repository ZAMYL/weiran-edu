import pytest

from app.core.errors import APIError
from app.storage.database import REPOSITORY, Database

from .conftest import commit


def test_migrations_are_idempotent_checksummed_and_foreign_keys_are_enabled(runtime):
    runtime.db.migrate()
    with runtime.db.transaction() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
    with runtime.db.transaction(write=True) as connection:
        connection.execute("UPDATE schema_migrations SET checksum='tampered'")
    with pytest.raises(ValueError, match="MIGRATION_CHECKSUM_MISMATCH"):
        runtime.db.migrate()


def test_storage_path_must_be_absolute_and_outside_repository(tmp_path):
    for path in (REPOSITORY / "unsafe.sqlite3", REPOSITORY / "apps/api/unsafe.sqlite3"):
        with pytest.raises(ValueError, match="DATABASE_PATH_MUST_BE_EXTERNAL_AND_ABSOLUTE"):
            Database(path)
    from pathlib import Path

    with pytest.raises(ValueError):
        Database(Path("relative.sqlite3"))


@pytest.mark.parametrize(
    "table",
    [
        "batches",
        "evaluations",
        "rubric_criteria",
        "batch_students",
        "batch_sources",
        "student_identities",
    ],
)
def test_confirmed_facts_cannot_be_updated_or_deleted(runtime, stage, table):
    commit(runtime, stage())
    for operation in ("DELETE FROM " + table, "UPDATE " + table + " SET rowid=rowid"):
        with pytest.raises(APIError) as error:
            with runtime.db.transaction(write=True) as connection:
                connection.execute(operation)
        assert error.value.status_code == 503


def test_raw_sources_and_identities_are_preserved_privately(runtime, stage, book, pack):
    original = pack(book)
    from app.edu.models import ImportMetadata

    view = runtime.service.create_import(
        runtime.p,
        original,
        ImportMetadata(
            data_scope_id=runtime.scope,
            title="合成来源保存检查",
        ),
    )
    batch = commit(runtime, view)
    with runtime.db.transaction() as connection:
        assert (
            connection.execute(
                "SELECT raw_xlsx FROM batch_sources WHERE batch_id=?", (batch.batch_id,)
            ).fetchone()[0]
            == original
        )
        assert connection.execute("SELECT COUNT(*) FROM student_identities").fetchone()[0] == 2
        assert (
            connection.execute("SELECT score_units FROM evaluations LIMIT 1").fetchone()[0] == 275
        )
        assert (
            connection.execute("SELECT comment_raw FROM evaluations LIMIT 1")
            .fetchone()[0]
            .startswith("合成评语")
        )


def test_read_transactions_reject_writes_and_missing_database_is_not_created(tmp_path, runtime):
    with pytest.raises(APIError):
        with runtime.db.transaction() as connection:
            connection.execute("DELETE FROM teacher_tokens")
    path = tmp_path / "not-initialized.sqlite3"
    with pytest.raises(APIError):
        with Database(path).transaction():
            pass
    assert not path.exists()
