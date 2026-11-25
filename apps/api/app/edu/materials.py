"""B-02-S1 trusted intake: hash-bound records and append-only confirmations.

Nothing here stores file bytes. A record keeps the structure snapshot that was
actually recognized, and a confirmation says which teacher, at what time, bound
a role or field meaning to that content hash. Re-uploading the same bytes is the
only way to prove the claim later; different bytes never inherit a confirmation.
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import get_args
from uuid import uuid4

from app.ai.edu.material_contract import Meaning, Role
from app.core.errors import APIError
from app.edu.auth import Principal, require_scope, require_teacher
from app.edu.material_models import (
    MaterialCapability,
    MaterialConfirmRequest,
    MaterialDecision,
    MaterialField,
    MaterialFile,
    MaterialRecordView,
    MaterialRole,
)
from app.storage.database import Database

ROLES = frozenset(get_args(Role))
MEANINGS = frozenset(get_args(Meaning))
PLAN_LIMIT = 256 * 1024
PLAN_VERSION = "material.v1"


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("UTC_CLOCK_REQUIRED")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _fail(status: int, code: str, message: str) -> None:
    raise APIError(status, code, message)


class MaterialIntake:
    """Persist recognized structure and teacher confirmations, never raw content."""

    def __init__(self, database: Database, *, clock: Callable[[], datetime] | None = None) -> None:
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> str:
        return _stamp(self.clock())

    def record(
        self, principal: Principal, entries: Sequence[tuple[str, MaterialFile]]
    ) -> list[str]:
        """One row per (sha256, owner); a repeat upload refreshes the snapshot only."""
        with self.database.transaction(write=True) as connection:
            require_teacher(connection, principal, self._now())
            material_ids = []
            for digest, file in entries:
                plan = json.dumps(file.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
                if len(plan.encode()) > PLAN_LIMIT:
                    _fail(413, "MATERIAL_PLAN_TOO_LARGE", "识别结构超过安全上限")
                existing = connection.execute(
                    "SELECT material_id FROM material_records "
                    "WHERE sha256=? AND owner_teacher_id=?",
                    (digest, principal.teacher_id),
                ).fetchone()
                if existing is None:
                    material_id = str(uuid4())
                    connection.execute(
                        "INSERT INTO material_records (material_id,sha256,source_id,"
                        "owner_teacher_id,data_scope_id,file_name,kind,byte_size,plan_json,"
                        "plan_version,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            material_id,
                            digest,
                            file.source_id,
                            principal.teacher_id,
                            None,
                            file.file_name,
                            file.kind,
                            file.byte_size,
                            plan,
                            PLAN_VERSION,
                            "active",
                            self._now(),
                        ),
                    )
                else:
                    material_id = existing["material_id"]
                    # 来源标识保持首次上传的规范值，重复上传只刷新结构快照。
                    connection.execute(
                        "UPDATE material_records SET file_name=?,kind=?,"
                        "byte_size=?,plan_json=?,plan_version=? WHERE material_id=?",
                        (
                            file.file_name,
                            file.kind,
                            file.byte_size,
                            plan,
                            PLAN_VERSION,
                            material_id,
                        ),
                    )
                material_ids.append(material_id)
            return material_ids

    def confirm(self, principal: Principal, request: MaterialConfirmRequest) -> MaterialRecordView:
        with self.database.transaction(write=True) as connection:
            require_teacher(connection, principal, self._now())
            row = self._owned(connection, principal, request.source_id)
            if row["status"] != "active":
                _fail(409, "MATERIAL_REVOKED", "该资料已撤回，确认不再生效")
            require_scope(connection, principal, request.data_scope_id, importing=True)
            if row["data_scope_id"] not in (None, request.data_scope_id):
                _fail(409, "MATERIAL_SCOPE_CONFLICT", "该资料已归属其他教学空间")
            self._validate(json.loads(row["plan_json"]), request.decisions)
            now = self._now()
            for decision in request.decisions:
                existing = self._confirmation(connection, row["material_id"], decision)
                if existing is None:
                    connection.execute(
                        "INSERT INTO material_confirmations (confirmation_id,material_id,kind,"
                        "table_ref,column_ref,value,confirmed_by,confirmed_at,basis) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            str(uuid4()),
                            row["material_id"],
                            decision.kind,
                            decision.table,
                            decision.column,
                            decision.value,
                            principal.teacher_id,
                            now,
                            "teacher_review",
                        ),
                    )
                elif existing["value"] != decision.value:
                    _fail(409, "CONFIRMATION_CONFLICT", "同一位置已有不同确认，请以新文件版本重试")
            connection.execute(
                "UPDATE material_records SET data_scope_id=? "
                "WHERE material_id=? AND data_scope_id IS NULL",
                (request.data_scope_id, row["material_id"]),
            )
            return self._view(connection, self._owned(connection, principal, request.source_id))

    def read(self, principal: Principal, source_id: str) -> MaterialRecordView:
        with self.database.transaction() as connection:
            require_teacher(connection, principal, self._now())
            return self._view(connection, self._owned(connection, principal, source_id))

    @staticmethod
    def _owned(connection: sqlite3.Connection, principal: Principal, source_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM material_records WHERE source_id=? AND owner_teacher_id=?",
            (source_id, principal.teacher_id),
        ).fetchone()
        if row is None:
            raise APIError(404, "MATERIAL_NOT_FOUND", "资料不存在")
        return row

    @staticmethod
    def _confirmation(
        connection: sqlite3.Connection, material_id: str, decision: MaterialDecision
    ) -> sqlite3.Row | None:
        if decision.kind == "role":
            return connection.execute(
                "SELECT value FROM material_confirmations WHERE material_id=? "
                "AND kind='role' AND table_ref=?",
                (material_id, decision.table),
            ).fetchone()
        return connection.execute(
            "SELECT value FROM material_confirmations WHERE material_id=? "
            "AND kind='field' AND table_ref=? AND column_ref=?",
            (material_id, decision.table, decision.column),
        ).fetchone()

    @staticmethod
    def _validate(plan: dict, decisions: Sequence[MaterialDecision]) -> None:
        columns = {
            table["ref"]: {column["ref"] for column in table.get("columns", [])}
            for table in plan.get("tables", [])
        }
        for decision in decisions:
            if decision.table not in columns:
                _fail(422, "MATERIAL_LOCATOR_UNKNOWN", "确认位置不在本次识别结果内")
            if decision.kind == "role":
                if decision.column is not None or decision.value not in ROLES:
                    _fail(422, "MATERIAL_VALUE_INVALID", "资料角色不在允许范围内")
            elif decision.column not in columns[decision.table] or decision.value not in MEANINGS:
                _fail(422, "MATERIAL_VALUE_INVALID", "字段位置或含义不在允许范围内")

    def _view(self, connection: sqlite3.Connection, row: sqlite3.Row) -> MaterialRecordView:
        file = MaterialFile.model_validate_json(row["plan_json"])
        rows = connection.execute(
            "SELECT kind,table_ref,column_ref,value FROM material_confirmations "
            "WHERE material_id=? ORDER BY confirmed_at,confirmation_id",
            (row["material_id"],),
        ).fetchall()
        # 后写覆盖先写：同一位置的新确认生效，旧记录仍留在表里可审计。
        latest = {
            (item["kind"], item["table_ref"], item["column_ref"] or ""): item["value"]
            for item in rows
        }
        confirmed = [
            MaterialDecision(kind=kind, table=table, column=column or None, value=value)
            for (kind, table, column), value in sorted(latest.items())
        ]
        headers = {column.ref: column.header for table in file.tables for column in table.columns}
        table_of_column = {
            column.ref: table.ref for table in file.tables for column in table.columns
        }
        roles_confirmed: dict[str, set[str]] = {}
        fields_confirmed: dict[str, set[str]] = {}
        for decision in confirmed:
            if decision.kind == "role":
                roles_confirmed.setdefault(decision.table, set()).add(decision.value)
            else:
                table_ref = table_of_column.get(decision.column or "", "")
                fields_confirmed.setdefault(table_ref, set()).add(decision.value)

        roles = [role.model_copy() for role in file.roles]
        present_roles = {(role.table, role.role) for role in roles}
        for role in roles:
            if role.role in roles_confirmed.get(role.table, set()):
                role.state = "confirmed"
                role.reasons = []
        for table_ref, values in roles_confirmed.items():
            for value in sorted(values):
                if (table_ref, value) not in present_roles:
                    roles.append(
                        MaterialRole(table=table_ref, role=value, state="confirmed", reasons=[])
                    )

        fields = [field.model_copy() for field in file.fields]
        present_fields = {(field.column, field.meaning) for field in fields}
        for field in fields:
            if field.meaning in fields_confirmed.get(table_of_column.get(field.column, ""), set()):
                field.state = "confirmed"
                field.reasons = []
        for table_ref, values in fields_confirmed.items():
            for value in sorted(values):
                for column_ref, column_table in table_of_column.items():
                    if column_table != table_ref or (column_ref, value) in present_fields:
                        continue
                    fields.append(
                        MaterialField(
                            column=column_ref,
                            header=headers.get(column_ref),
                            meaning=value,
                            state="confirmed",
                            reasons=[],
                        )
                    )

        confirmed_refs = {decision.table for decision in confirmed if decision.kind == "role"}
        confirmed_refs |= {decision.column for decision in confirmed if decision.kind == "field"}
        pending = [ref for ref in file.pending if ref not in confirmed_refs]
        analyses = [
            self._analysis(capability, roles_confirmed, fields_confirmed)
            for capability in file.analyses
        ]
        return MaterialRecordView(
            material_id=row["material_id"],
            source_id=row["source_id"],
            status=row["status"],
            data_scope_id=row["data_scope_id"],
            created_at=row["created_at"],
            file=file.model_copy(
                update={
                    "roles": roles,
                    "fields": fields,
                    "pending": pending,
                    "analyses": analyses,
                }
            ),
            confirmed=confirmed,
        )

    @staticmethod
    def _analysis(
        capability: MaterialCapability,
        roles_confirmed: dict[str, set[str]],
        fields_confirmed: dict[str, set[str]],
    ) -> MaterialCapability:
        table = capability.table

        def satisfied(reason: str) -> bool:
            if table is None:
                return False
            if reason.startswith("confirmed_role:"):
                return reason.split(":", 1)[1] in roles_confirmed.get(table, set())
            if reason.startswith("confirmed_field:"):
                return reason.split(":", 1)[1] in fields_confirmed.get(table, set())
            return False

        missing = [reason for reason in capability.missing if not satisfied(reason)]
        return capability.model_copy(
            update={"missing": missing, "available": table is not None and not missing}
        )
