"""Read-only structural readiness; never initialize, migrate or expose configuration."""

import hashlib
import re
from pathlib import Path

from app.core.errors import APIError

from .database import Database

CREATE_OBJECT = re.compile(
    r"\bCREATE\s+(TABLE|TRIGGER|(?:UNIQUE\s+)?INDEX)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def readiness_failure(path: Path | None) -> str | None:
    """Return a fixed operator-only reason, or None when structurally ready."""
    if path is None:
        return "DATABASE_NOT_CONFIGURED"
    try:
        files = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
        if not files:
            return "MIGRATION_MANIFEST_UNAVAILABLE"
        expected, objects = {}, {("table", "schema_migrations")}
        for file in files:
            sql = file.read_text(encoding="utf-8")
            expected[file.name] = hashlib.sha256(sql.encode()).hexdigest()
            objects.update(
                (kind.lower().split()[-1], name) for kind, name in CREATE_OBJECT.findall(sql)
            )
        with Database(path).transaction() as connection:
            connection.execute("SELECT 1").fetchone()
            rows = connection.execute("SELECT version,checksum FROM schema_migrations").fetchall()
            if (
                len(rows) != len(expected)
                or {r["version"]: r["checksum"] for r in rows} != expected
            ):
                return "MIGRATION_MISMATCH"
            actual = {
                (r["type"], r["name"])
                for r in connection.execute(
                    "SELECT type,name FROM sqlite_master WHERE type IN ('table','index','trigger')"
                )
            }
            if not objects <= actual:
                return "SCHEMA_OBJECT_MISSING"
    except (APIError, OSError, ValueError):
        return "DATABASE_UNAVAILABLE"
    return None
