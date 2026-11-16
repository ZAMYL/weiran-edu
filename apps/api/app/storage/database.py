"""Explicit transactions, external storage paths, checksummed SQL migrations."""

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.core.errors import APIError

REPOSITORY = Path(__file__).resolve().parents[4]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path) -> None:
        path = path.expanduser()
        if not path.is_absolute() or path.resolve().is_relative_to(REPOSITORY):
            raise ValueError("DATABASE_PATH_MUST_BE_EXTERNAL_AND_ABSOLUTE")
        self.path = path.resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path.as_uri() + "?mode=rw", uri=True, timeout=3, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=3000")
        connection.execute("PRAGMA trusted_schema=OFF")
        return connection

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = self._connect()
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise APIError(503, "DATABASE_UNAVAILABLE", "数据服务暂不可用") from None
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def rebuild(self, statements: tuple[str, ...]) -> None:
        """Operator-only table rebuild so a CHECK constraint can change in place.

        Every other path keeps foreign keys on; this one disables them only for the
        rebuild and re-verifies the whole database before committing.
        """
        connection = self._connect()
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            for statement in statements:
                connection.execute(statement)
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("DATABASE_INTEGRITY_FAILED")
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise APIError(503, "DATABASE_UNAVAILABLE", "数据服务暂不可用") from None
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        """CLI-only initialization; never implicitly migrate on an HTTP request."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("xb"):
                pass
        except FileExistsError:
            pass
        with self.transaction(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"
            )
            migrations = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
            expected = {file.name for file in migrations}
            known = connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
            if any(row["version"] not in expected for row in known):
                raise ValueError("UNSUPPORTED_DATABASE_VERSION")
            for file in migrations:
                sql = file.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                applied = next((row for row in known if row["version"] == file.name), None)
                if applied:
                    if applied["checksum"] != checksum:
                        raise ValueError("MIGRATION_CHECKSUM_MISMATCH")
                    continue
                # executescript implicitly commits: execute complete statements individually.
                statement = ""
                for line in sql.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError("INCOMPLETE_MIGRATION")
                connection.execute(
                    "INSERT INTO schema_migrations VALUES (?,?,?)", (file.name, checksum, utc_now())
                )
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("DATABASE_INTEGRITY_FAILED")
        if os.name != "nt":
            self.path.chmod(0o600)
