"""009 迁移：只做加法，旧库升级后历史行保持可读。"""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.storage import database as database_module
from app.storage.database import Database


def apply_legacy_schema(path: Path, *, before: str = "009") -> None:
    """按 001–008 建一个"旧库"，用来验证升级到 009 的兼容性。"""
    folder = Path(database_module.__file__).parent / "migrations"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL, "
        "applied_at TEXT NOT NULL) STRICT"
    )
    for file in sorted(folder.glob("*.sql")):
        if file.name >= before:
            continue
        sql = file.read_text(encoding="utf-8")
        statement = ""
        for line in sql.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement = ""
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?,?,?)",
            (file.name, hashlib.sha256(sql.encode()).hexdigest(), "2026-01-01T00:00:00Z"),
        )
    connection.commit()
    connection.close()


def insert_legacy_assessment(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO teachers (teacher_id, auth_issuer, auth_subject, is_active, created_at) "
        "VALUES ('t1', 'test', 't1', 1, '2026-01-01')"
    )
    connection.execute(
        "INSERT INTO data_scopes (data_scope_id, label, created_at) "
        "VALUES ('s1', '旧范围', '2026-01-01')"
    )
    connection.execute(
        "INSERT INTO students (student_pk, public_id, data_scope_id, created_at) "
        "VALUES ('st1', 'pub1', 's1', '2026-01-01')"
    )
    connection.execute(
        "INSERT INTO assessments (assessment_id, data_scope_id, owner_teacher_id, title, course, "
        "assessment_date, file_sha256, parser_version, student_count, problem_count, "
        "submission_count, observation_count, created_at) VALUES "
        "('a1', 's1', 't1', '旧批次', NULL, NULL, '" + "a" * 64 + "', 'answersheet.v1', "
        "1, 1, 1, 1, '2026-01-01')"
    )
    connection.execute(
        "INSERT INTO assessment_participants (assessment_id, student_pk, external_key, "
        "display_name) VALUES ('a1', 'st1', 'S001', '旧姓名')"
    )
    connection.execute(
        "INSERT INTO problems (problem_id, assessment_id, label, ordinal, total_score) "
        "VALUES ('p1', 'a1', '7-1', 0, 500)"
    )
    connection.execute(
        "INSERT INTO submissions (submission_id, assessment_id, student_pk, problem_id, language, "
        "code_text, verdict, score_units, score_total_units, created_at) VALUES "
        "('sub1', 'a1', 'st1', 'p1', 'GXX', 'int main(){}', '答案正确', 500, 500, '2026-01-01')"
    )
    connection.execute(
        "INSERT INTO test_observations (observation_id, submission_id, test_index, status, "
        "score_units, duration_ms, memory_kb) VALUES ('o1', 'sub1', 1, '答案正确', 500, 2, 304)"
    )
    connection.commit()
    connection.close()


def test_upgrade_from_008_keeps_rows_and_defaults(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    apply_legacy_schema(path)
    insert_legacy_assessment(path)

    Database(path).migrate()

    upgraded = Database(path)
    with upgraded.transaction() as connection:
        assessment = connection.execute(
            "SELECT * FROM assessments WHERE assessment_id='a1'"
        ).fetchone()
        assert assessment["review_state"] == "confirmed"
        assert assessment["order_basis"] == "NATURAL_SORT"
        submission = connection.execute(
            "SELECT * FROM submissions WHERE submission_id='sub1'"
        ).fetchone()
        assert submission["code_text"] == "int main(){}"
        assert submission["code_state"] == "PRESENT"
        assert submission["verdict_class"] is None
        observation = connection.execute(
            "SELECT * FROM test_observations WHERE observation_id='o1'"
        ).fetchone()
        assert observation["duration_ms"] == 2
        assert observation["duration_us"] is None
        assert observation["status_class"] is None
        problem = connection.execute("SELECT * FROM problems WHERE problem_id='p1'").fetchone()
        assert problem["total_score"] == 500
        assert problem["total_score_conflict"] == 0


def test_fresh_database_has_provenance_tables(tmp_path):
    database = Database(tmp_path / "fresh.sqlite3")
    database.migrate()
    with database.transaction() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"assessment_assets", "assessment_materials", "assessment_diagnostics"} <= names


def test_material_category_is_constrained(tmp_path):
    database = Database(tmp_path / "checks.sqlite3")
    database.migrate()
    connection = sqlite3.connect(database.path)
    connection.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO assessment_materials (material_id, assessment_id, entry, category, basis) "
            "VALUES ('m1', 'a1', 'e', 'NOT_A_CATEGORY', 'b')"
        )
    connection.close()


def test_migration_is_idempotent(tmp_path):
    database = Database(tmp_path / "repeat.sqlite3")
    database.migrate()
    database.migrate()
    with database.transaction() as connection:
        count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 14
