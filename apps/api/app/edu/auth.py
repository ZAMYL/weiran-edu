"""Revocable pilot credentials; only digests are persisted. No public issuer API."""

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.errors import APIError
from app.storage.database import Database, utc_now


@dataclass(frozen=True, repr=False)
class Principal:
    teacher_id: str
    token_hash: str


def deny(status: int, code: str = "RESOURCE_NOT_FOUND") -> None:
    messages = {
        401: "需要有效的教师凭证",
        403: "没有来源范围的导入权限",
        404: "资源不存在",
    }
    raise APIError(status, code, messages[status])


def require_teacher(connection: sqlite3.Connection, principal: Principal, now: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM teacher_tokens k JOIN teachers t ON t.teacher_id=k.teacher_id "
        "WHERE k.token_hash=? AND k.teacher_id=? AND k.revoked=0 AND k.expires_at>? "
        "AND t.is_active=1",
        (principal.token_hash, principal.teacher_id, now),
    ).fetchone()
    if row is None:
        deny(401, "UNAUTHORIZED")


def require_scope(
    connection: sqlite3.Connection,
    principal: Principal,
    scope_id: str,
    *,
    importing: bool = False,
) -> None:
    row = connection.execute(
        "SELECT can_import FROM scope_members WHERE teacher_id=? AND data_scope_id=?",
        (principal.teacher_id, scope_id),
    ).fetchone()
    if row is None or importing and not row["can_import"]:
        deny(403 if importing else 404, "FORBIDDEN" if importing else "RESOURCE_NOT_FOUND")


def accessible_batch(
    connection: sqlite3.Connection,
    principal: Principal,
    batch_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT b.*, r.rubric_version, NOT EXISTS(SELECT 1 FROM batches n "
        "WHERE n.revision_of_batch_id=b.batch_id) AS is_current "
        "FROM batches b JOIN rubric_snapshots r ON r.rubric_id=b.rubric_id "
        "JOIN scope_members m ON m.data_scope_id=b.data_scope_id AND m.teacher_id=? "
        "WHERE b.batch_id=? AND (b.owner_teacher_id=? OR EXISTS(SELECT 1 FROM batch_access a "
        "WHERE a.batch_id=b.batch_id AND a.teacher_id=? AND a.permission='read'))",
        (principal.teacher_id, batch_id, principal.teacher_id, principal.teacher_id),
    ).fetchone()
    if row is None:
        deny(404)
    return row


class TeacherAdmin:
    """Server operator API only. Never expose these methods through a teacher route."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_teacher(self, subject: str, *, issuer: str = "local-pilot") -> str:
        if not subject.strip() or len(subject) > 200 or not issuer.strip() or len(issuer) > 200:
            raise ValueError("INVALID_TEACHER_SUBJECT")
        teacher_id = str(uuid4())
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO teachers VALUES (?,?,?,?,?)",
                (teacher_id, issuer, subject, 1, utc_now()),
            )
        return teacher_id

    def issue_token(self, teacher_id: str, *, hours: int = 24) -> str:
        if type(hours) is not int or not 1 <= hours <= 168:
            raise ValueError("TOKEN_LIFETIME_INVALID")
        token = "wedu_" + secrets.token_urlsafe(32)
        expires = (
            (datetime.now(UTC) + timedelta(hours=hours))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT is_active FROM teachers WHERE teacher_id=?", (teacher_id,)
            ).fetchone()
            if row is None or not row["is_active"]:
                raise ValueError("TEACHER_UNAVAILABLE")
            connection.execute(
                "INSERT INTO teacher_tokens VALUES (?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), teacher_id, expires, 0, utc_now()),
            )
        return token

    def revoke_tokens(self, teacher_id: str) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE teacher_tokens SET revoked=1 WHERE teacher_id=?", (teacher_id,)
            )

    def set_active(self, teacher_id: str, active: bool) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE teachers SET is_active=? WHERE teacher_id=?", (int(active), teacher_id)
            )
            if not active:
                connection.execute(
                    "UPDATE teacher_tokens SET revoked=1 WHERE teacher_id=?", (teacher_id,)
                )

    def create_scope(self, label: str) -> str:
        if not label.strip() or len(label) > 100:
            raise ValueError("INVALID_SCOPE_LABEL")
        scope_id = str(uuid4())
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO data_scopes VALUES (?,?,?)", (scope_id, label, utc_now())
            )
        return scope_id

    def set_member(self, teacher_id: str, scope_id: str, *, can_import: bool) -> None:
        if type(can_import) is not bool:
            raise ValueError("INVALID_PERMISSION")
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO scope_members VALUES (?,?,?) ON CONFLICT(data_scope_id,teacher_id) "
                "DO UPDATE SET can_import=excluded.can_import",
                (scope_id, teacher_id, int(can_import)),
            )

    def remove_member(self, teacher_id: str, scope_id: str) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "DELETE FROM batch_access WHERE teacher_id=? AND batch_id IN "
                "(SELECT batch_id FROM batches WHERE data_scope_id=?)",
                (teacher_id, scope_id),
            )
            connection.execute(
                "DELETE FROM scope_members WHERE teacher_id=? AND data_scope_id=?",
                (teacher_id, scope_id),
            )

    def grant_read(self, teacher_id: str, batch_id: str) -> None:
        with self.database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT data_scope_id FROM batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if (
                row is None
                or connection.execute(
                    "SELECT 1 FROM scope_members WHERE teacher_id=? AND data_scope_id=?",
                    (teacher_id, row["data_scope_id"]),
                ).fetchone()
                is None
            ):
                raise ValueError("SCOPE_MEMBERSHIP_REQUIRED")
            connection.execute(
                "INSERT OR IGNORE INTO batch_access VALUES (?,?,'read',?)",
                (batch_id, teacher_id, utc_now()),
            )

    def revoke_read(self, teacher_id: str, batch_id: str) -> None:
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "DELETE FROM batch_access WHERE teacher_id=? AND batch_id=?", (teacher_id, batch_id)
            )


def authenticate(database: Database, token: str) -> Principal:
    if not token.startswith("wedu_") or not 40 <= len(token) <= 100:
        deny(401, "UNAUTHORIZED")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT teacher_id FROM teacher_tokens WHERE token_hash=?", (digest,)
        ).fetchone()
        if row is None:
            deny(401, "UNAUTHORIZED")
        principal = Principal(row["teacher_id"], digest)
        require_teacher(connection, principal, utc_now())
        return principal
