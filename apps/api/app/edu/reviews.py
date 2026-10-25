"""Owner-only text review with append-only publication and withdrawal history."""

import json
import sqlite3
from uuid import uuid4

from app.core.errors import APIError

from .auth import Principal, deny
from .privacy import checked_text, redact_text
from .reads import BatchReader
from .review_models import TextKind, TextReview, TextReviewPreview, TextReviewWrite
from .service import stamp


def require_owner(batch: sqlite3.Row, principal: Principal) -> None:
    if batch["owner_teacher_id"] != principal.teacher_id:
        raise APIError(403, "FORBIDDEN", "仅批次拥有者可审核或修改结果")


def version_conflict(code: str) -> None:
    raise APIError(409, code, "版本已变化，请重新读取后重试")


class TextReviewer(BatchReader):
    @staticmethod
    def _source(
        connection: sqlite3.Connection, batch: sqlite3.Row, kind: TextKind, target_id: str
    ) -> str:
        if kind == "comment":
            row = connection.execute(
                "SELECT comment_raw FROM evaluations WHERE batch_id=? AND evidence_id=?",
                (batch["batch_id"], target_id),
            ).fetchone()
            if row is None:
                deny(404)
            return row["comment_raw"]
        row = connection.execute(
            "SELECT text_cells_json FROM rubric_criteria "
            "WHERE rubric_id=? AND rubric_criterion_id=?",
            (batch["rubric_id"], target_id),
        ).fetchone()
        if row is None:
            deny(404)
        return "\n".join(value for _, value in json.loads(row["text_cells_json"]))

    @staticmethod
    def _current(
        connection: sqlite3.Connection,
        batch: sqlite3.Row,
        kind: TextKind,
        target_id: str,
        *,
        empty: bool,
    ) -> TextReview:
        row = connection.execute(
            "SELECT * FROM text_reviews WHERE batch_id=? AND target_kind=? AND target_id=? "
            "ORDER BY version DESC LIMIT 1",
            (batch["batch_id"], kind, target_id),
        ).fetchone()
        return TextReview(
            batch_id=batch["batch_id"],
            snapshot_id=batch["snapshot_id"],
            target_kind=kind,
            target_id=target_id,
            version=row["version"] if row else 0,
            status=row["status"] if row else "empty" if empty and kind == "comment" else "withheld",
            display_text=row["display_text"] if row else None,
            review_id=row["review_id"] if row else None,
            reviewed_by=row["reviewed_by"] if row else None,
            reviewed_at=row["created_at"] if row else None,
        )

    def preview(
        self,
        principal: Principal,
        batch_id: str,
        kind: TextKind,
        target_id: str,
        *,
        snapshot_id: str,
    ) -> TextReviewPreview:
        with self.database.transaction() as connection:
            batch = self._batch(connection, principal, batch_id, snapshot_id)
            require_owner(batch, principal)
            original = self._source(connection, batch, kind, target_id)
            redacted = redact_text(connection, batch["data_scope_id"], original)
            current = self._current(connection, batch, kind, target_id, empty=not original.strip())
            return TextReviewPreview(
                **current.model_dump(),
                preview_text=redacted[:4000] or None,
                preview_truncated=len(redacted) > 4000,
            )

    def review(
        self,
        principal: Principal,
        batch_id: str,
        kind: TextKind,
        target_id: str,
        body: TextReviewWrite,
    ) -> TextReview:
        with self.database.transaction(write=True) as connection:
            batch = self._batch(connection, principal, batch_id, body.snapshot_id)
            require_owner(batch, principal)
            original = self._source(connection, batch, kind, target_id)
            current = self._current(connection, batch, kind, target_id, empty=not original.strip())
            if body.expected_version != current.version:
                version_conflict("TEXT_REVIEW_VERSION_MISMATCH")
            text = None
            if body.status == "published":
                if not original.strip():
                    raise APIError(409, "REVIEW_SOURCE_EMPTY", "原文为空，不能发布为来源评语")
                text = checked_text(connection, batch["data_scope_id"], body.display_text)
                if not text:
                    raise APIError(422, "VALIDATION_ERROR", "发布文本不能为空")
            connection.execute(
                "INSERT INTO text_reviews VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    batch_id,
                    batch["snapshot_id"],
                    kind,
                    target_id,
                    current.version + 1,
                    body.status,
                    text,
                    principal.teacher_id,
                    stamp(self.clock()),
                ),
            )
            return self._current(connection, batch, kind, target_id, empty=False)
