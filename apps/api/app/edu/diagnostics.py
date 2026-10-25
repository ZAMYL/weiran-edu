"""Authorized metadata diagnostics that also work for damaged fact snapshots."""

import sqlite3
from collections.abc import Collection

from app.core.errors import APIError

from .auth import Principal, accessible_batch, require_teacher
from .diagnostic_models import (
    Action,
    BatchCapabilities,
    Capability,
    IntegrityReport,
    IntegrityRequest,
)
from .integrity import inspect_snapshot
from .reads import BatchReader
from .service import fail, stamp


class DiagnosticReader(BatchReader):
    def _authorized(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        batch_id: str,
        snapshot_id: str | None,
        *,
        owner_only: bool = False,
    ) -> sqlite3.Row:
        require_teacher(connection, principal, stamp(self.clock()))
        batch = accessible_batch(connection, principal, batch_id)
        if owner_only and batch["owner_teacher_id"] != principal.teacher_id:
            raise APIError(403, "FORBIDDEN", "仅批次拥有者可查看完整性诊断")
        if snapshot_id is not None and snapshot_id != batch["snapshot_id"]:
            fail("SNAPSHOT_MISMATCH")
        # Do not use _batch: it deliberately rejects damaged facts before reporting.
        return batch

    def capabilities(
        self,
        principal: Principal,
        batch_id: str,
        *,
        snapshot_id: str | None = None,
        available_features: Collection[Action] = (),
    ) -> BatchCapabilities:
        with self.database.transaction() as connection:
            batch = self._authorized(connection, principal, batch_id, snapshot_id)
            owner = batch["owner_teacher_id"] == principal.teacher_id
            importing = (
                connection.execute(
                    "SELECT can_import FROM scope_members WHERE teacher_id=? AND data_scope_id=?",
                    (principal.teacher_id, batch["data_scope_id"]),
                ).fetchone()[0]
                == 1
            )
            operations = []
            for action, available, permission, denied in (
                ("read_batch", True, True, "owner_required"),
                ("import_data", True, importing, "import_permission_required"),
                ("diagnose_integrity", True, owner, "owner_required"),
                ("review_text", "review_text" in available_features, owner, "owner_required"),
                ("save_analysis", "save_analysis" in available_features, owner, "owner_required"),
                ("edit_analysis", "edit_analysis" in available_features, owner, "owner_required"),
                (
                    "export_analysis",
                    "export_analysis" in available_features,
                    True,
                    "owner_required",
                ),
            ):
                operations.append(
                    Capability(
                        action=action,
                        available=available,
                        allowed=available and permission,
                        reason="feature_unavailable"
                        if not available
                        else "allowed"
                        if permission
                        else denied,
                    )
                )
            return BatchCapabilities(
                batch_id=batch_id,
                snapshot_id=batch["snapshot_id"],
                role="owner" if owner else "reader",
                operations=tuple(operations),
            )

    def integrity(
        self, principal: Principal, batch_id: str, body: IntegrityRequest
    ) -> IntegrityReport:
        with self.database.transaction() as connection:
            batch = self._authorized(
                connection, principal, batch_id, body.snapshot_id, owner_only=True
            )
            inspection = inspect_snapshot(connection, batch)
            return IntegrityReport(
                **inspection.model_dump(),
                batch_id=batch_id,
                snapshot_id=batch["snapshot_id"],
                checked_at=stamp(self.clock()),
            )
