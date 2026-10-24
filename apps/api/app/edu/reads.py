"""Snapshot-consistent reads with current teacher and resource authorization."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from app.importing.edu.preview import PreviewSource, validate_page
from app.importing.edu.types import DIMENSIONS
from app.storage.database import Database

from .auth import Principal, accessible_batch, deny, require_teacher
from .models import (
    Evidence,
    Rubric,
    RubricCriterion,
    RubricSource,
    Student,
    StudentEvidence,
    StudentList,
    StudentScore,
)
from .service import _page, fail, stamp

ORDER = {spec.code: index for index, spec in enumerate(DIMENSIONS)}


class BatchReader:
    def __init__(
        self, database: Database, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.database, self.clock = database, clock

    def _batch(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        batch_id: str,
        snapshot_id: str | None,
    ) -> sqlite3.Row:
        require_teacher(connection, principal, stamp(self.clock()))
        row = accessible_batch(connection, principal, batch_id)
        if snapshot_id is not None and snapshot_id != row["snapshot_id"]:
            fail("SNAPSHOT_MISMATCH")
        return row

    def students(
        self,
        principal: Principal,
        batch_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
        snapshot_id: str | None = None,
    ) -> StudentList:
        validate_page(offset, limit)
        with self.database.transaction() as connection:
            batch = self._batch(connection, principal, batch_id, snapshot_id)
            count = connection.execute(
                "SELECT COUNT(*) FROM batch_students WHERE batch_id=?", (batch_id,)
            ).fetchone()[0]
            if count != batch["student_count"]:
                fail("DATA_INTEGRITY_ERROR", 503)
            rows = connection.execute(
                "SELECT s.student_pk,s.public_id,bs.display_label FROM batch_students bs "
                "JOIN students s ON s.student_pk=bs.student_pk WHERE bs.batch_id=? "
                "ORDER BY bs.display_order,s.public_id LIMIT ? OFFSET ?",
                (batch_id, limit, offset),
            ).fetchall()
            items = []
            for row in rows:
                scores = connection.execute(
                    "SELECT dimension_code,score_units,evidence_id FROM evaluations "
                    "WHERE batch_id=? AND student_pk=?",
                    (batch_id, row["student_pk"]),
                ).fetchall()
                if len(scores) != 5 or {s["dimension_code"] for s in scores} != set(ORDER):
                    fail("DATA_INTEGRITY_ERROR", 503)
                items.append(
                    Student(
                        student_id=row["public_id"],
                        display_label=row["display_label"],
                        scores=tuple(
                            StudentScore(
                                dimension_code=s["dimension_code"],
                                score=s["score_units"] / 100,
                                evidence_id=s["evidence_id"],
                            )
                            for s in sorted(scores, key=lambda s: ORDER[s["dimension_code"]])
                        ),
                    )
                )
            return StudentList(
                batch_id=batch_id,
                snapshot_id=batch["snapshot_id"],
                total_count=count,
                items=tuple(items),
                page=_page(offset, limit, len(items), count),
            )

    @staticmethod
    def _evidence_rows() -> str:
        # Static SQL: caller appends only fixed predicates, never request strings.
        return (
            "SELECT e.evidence_id,e.batch_id,s.public_id,e.dimension_code,"
            "e.score_units,e.score_raw,b.snapshot_id,b.file_sha256 AS batch_file_sha256,"
            "r.file_sha256 AS rubric_file_sha256,"
            "e.comment_status,e.file_sha256,e.sheet_name,e.source_row,e.dimension_cell,e.score_cell,"
            "e.comment_cell,r.rubric_criterion_id FROM evaluations e "
            "JOIN students s ON s.student_pk=e.student_pk "
            "JOIN batches b ON b.batch_id=e.batch_id AND b.rubric_id=e.rubric_id "
            "JOIN rubric_criteria r ON r.rubric_id=e.rubric_id "
            "AND r.dimension_code=e.dimension_code "
            "WHERE e.batch_id=?"
        )

    @staticmethod
    def _evidence(row: sqlite3.Row) -> Evidence:
        if (
            row["dimension_code"] not in ORDER
            or not 100 <= row["score_units"] <= 500
            or row["file_sha256"] != row["batch_file_sha256"]
            or row["rubric_file_sha256"] != row["batch_file_sha256"]
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        return Evidence(
            evidence_id=row["evidence_id"],
            batch_id=row["batch_id"],
            snapshot_id=row["snapshot_id"],
            student_id=row["public_id"],
            dimension_code=row["dimension_code"],
            score=row["score_units"] / 100,
            score_raw=row["score_raw"],
            comment_status=row["comment_status"],
            source=PreviewSource(
                file_sha256=row["file_sha256"],
                sheet_name=row["sheet_name"],
                row_number=row["source_row"],
                dimension_cell=row["dimension_cell"],
                score_cell=row["score_cell"],
                comment_cell=row["comment_cell"],
            ),
            rubric_criterion_id=row["rubric_criterion_id"],
        )

    def evidence(
        self,
        principal: Principal,
        batch_id: str,
        evidence_id: str,
        *,
        snapshot_id: str | None = None,
    ) -> Evidence:
        with self.database.transaction() as connection:
            self._batch(connection, principal, batch_id, snapshot_id)
            row = connection.execute(
                self._evidence_rows() + " AND e.evidence_id=?",
                (batch_id, evidence_id),
            ).fetchone()
            if row is None:
                deny(404)
            return self._evidence(row)

    def student_evidence(
        self,
        principal: Principal,
        batch_id: str,
        student_id: str,
        *,
        snapshot_id: str | None = None,
    ) -> StudentEvidence:
        with self.database.transaction() as connection:
            batch = self._batch(connection, principal, batch_id, snapshot_id)
            student = connection.execute(
                "SELECT bs.display_label FROM batch_students bs "
                "JOIN students s ON s.student_pk=bs.student_pk "
                "WHERE bs.batch_id=? AND s.public_id=?",
                (batch_id, student_id),
            ).fetchone()
            if student is None:
                deny(404)
            rows = connection.execute(
                self._evidence_rows() + " AND s.public_id=?",
                (batch_id, student_id),
            ).fetchall()
            if len(rows) != 5 or {r["dimension_code"] for r in rows} != set(ORDER):
                fail("DATA_INTEGRITY_ERROR", 503)
            return StudentEvidence(
                batch_id=batch_id,
                snapshot_id=batch["snapshot_id"],
                student_id=student_id,
                display_label=student["display_label"],
                rubric_version=batch["rubric_version"],
                items=tuple(
                    self._evidence(r)
                    for r in sorted(rows, key=lambda r: ORDER[r["dimension_code"]])
                ),
            )

    def rubric(
        self, principal: Principal, batch_id: str, *, snapshot_id: str | None = None
    ) -> Rubric:
        with self.database.transaction() as connection:
            batch = self._batch(connection, principal, batch_id, snapshot_id)
            rows = connection.execute(
                "SELECT rubric_criterion_id,dimension_code,file_sha256,sheet_name,source_range "
                "FROM rubric_criteria WHERE rubric_id=?",
                (batch["rubric_id"],),
            ).fetchall()
            if (
                len(rows) != 5
                or {r["dimension_code"] for r in rows} != set(ORDER)
                or any(r["file_sha256"] != batch["file_sha256"] for r in rows)
            ):
                fail("DATA_INTEGRITY_ERROR", 503)
            by_code = {row["dimension_code"]: row for row in rows}
            return Rubric(
                batch_id=batch_id,
                snapshot_id=batch["snapshot_id"],
                rubric_id=batch["rubric_id"],
                rubric_version=batch["rubric_version"],
                criteria=tuple(
                    RubricCriterion(
                        rubric_criterion_id=by_code[spec.code]["rubric_criterion_id"],
                        dimension_code=spec.code,
                        display_name=spec.label,
                        original_name=spec.rubric_title,
                        source=RubricSource(
                            file_sha256=by_code[spec.code]["file_sha256"],
                            sheet_name=by_code[spec.code]["sheet_name"],
                            range=by_code[spec.code]["source_range"],
                        ),
                    )
                    for spec in DIMENSIONS
                ),
            )
