"""Optional operator-initialized telemetry DB, separate from assessment storage."""

import hashlib
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.errors import APIError

from .database import Database

RETENTION_DAYS = 7
MAX_RECORDS = 20_000
MAX_PER_TEACHER = 1_000
REQUEST_ID = re.compile(r"req_[0-9a-f]{32}\Z")
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS request_trace_schema "
    "(version TEXT PRIMARY KEY,checksum TEXT NOT NULL) STRICT",
    "CREATE TABLE IF NOT EXISTS request_traces "
    "(request_id TEXT PRIMARY KEY,teacher_id TEXT NOT NULL,"
    "route_template TEXT NOT NULL,method TEXT NOT NULL,status_code INTEGER NOT NULL,"
    "error_code TEXT,stage TEXT NOT NULL,duration_ms INTEGER NOT NULL,created_at TEXT NOT NULL,"
    "expires_at TEXT NOT NULL) STRICT",
    "CREATE INDEX IF NOT EXISTS traces_teacher ON request_traces(teacher_id,created_at,request_id)",
    "CREATE INDEX IF NOT EXISTS traces_expiry ON request_traces(expires_at)",
)
CHECKSUM = hashlib.sha256("\n".join(SCHEMA).encode()).hexdigest()


def utc_stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class TraceDatabase(Database):
    def _connect(self) -> sqlite3.Connection:
        connection = super()._connect()
        connection.execute("PRAGMA busy_timeout=100")
        return connection


class RequestTraceStore:
    def __init__(self, path: Path) -> None:
        self.database = TraceDatabase(path)

    @staticmethod
    def _schema(connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT version,checksum FROM request_trace_schema").fetchall()
        if len(rows) != 1 or tuple(rows[0]) != ("trace.v1", CHECKSUM):
            raise APIError(503, "REQUEST_TRACE_UNAVAILABLE", "请求诊断记录暂不可用")

    def initialize(self) -> None:
        """Operator CLI only; refuse to add telemetry tables to a business DB."""
        self.database.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.database.path.open("xb"):
                pass
        except FileExistsError:
            pass
        with self.database.transaction(write=True) as connection:
            tables = {
                r[0]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if tables - {"request_trace_schema", "request_traces"}:
                raise ValueError("DEDICATED_TRACE_DATABASE_REQUIRED")
            for statement in SCHEMA:
                connection.execute(statement)
            connection.execute(
                "INSERT OR IGNORE INTO request_trace_schema VALUES (?,?)", ("trace.v1", CHECKSUM)
            )
            self._schema(connection)
        if os.name != "nt":
            self.database.path.chmod(0o600)

    def record(
        self,
        *,
        request_id: str,
        teacher_id: str,
        route_template: str,
        method: str,
        status_code: int,
        error_code: str | None,
        stage: str,
        duration_ms: int,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        try:
            with self.database.transaction(write=True) as connection:
                self._schema(connection)
                created = utc_stamp(now)
                connection.execute("DELETE FROM request_traces WHERE expires_at<=?", (created,))
                connection.execute(
                    "INSERT INTO request_traces VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        teacher_id,
                        route_template,
                        method,
                        status_code,
                        error_code,
                        stage,
                        max(0, min(duration_ms, 3_600_000)),
                        created,
                        utc_stamp(now + timedelta(days=RETENTION_DAYS)),
                    ),
                )
                connection.execute(
                    "DELETE FROM request_traces WHERE request_id IN "
                    "(SELECT request_id FROM request_traces WHERE teacher_id=? "
                    "ORDER BY created_at DESC,request_id DESC LIMIT -1 OFFSET ?)",
                    (teacher_id, MAX_PER_TEACHER),
                )
                connection.execute(
                    "DELETE FROM request_traces WHERE request_id IN "
                    "(SELECT request_id FROM request_traces "
                    "ORDER BY created_at DESC,request_id DESC LIMIT -1 OFFSET ?)",
                    (MAX_RECORDS,),
                )
            return True
        except (APIError, OSError, ValueError):
            return False

    def get(self, teacher_id: str, request_id: str, *, now: datetime | None = None) -> dict | None:
        if not REQUEST_ID.fullmatch(request_id):
            return None
        try:
            with self.database.transaction() as connection:
                self._schema(connection)
                row = connection.execute(
                    "SELECT request_id,route_template,method,status_code,error_code,stage,"
                    "duration_ms,"
                    "created_at,expires_at FROM request_traces "
                    "WHERE teacher_id=? AND request_id=? AND expires_at>?",
                    (teacher_id, request_id, utc_stamp(now or datetime.now(UTC))),
                ).fetchone()
                return dict(row) if row else None
        except APIError:
            raise APIError(503, "REQUEST_TRACE_UNAVAILABLE", "请求诊断记录暂不可用") from None

    def cleanup(self) -> int:
        with self.database.transaction(write=True) as connection:
            self._schema(connection)
            cursor = connection.execute(
                "DELETE FROM request_traces WHERE expires_at<=?", (utc_stamp(datetime.now(UTC)),)
            )
            return cursor.rowcount
