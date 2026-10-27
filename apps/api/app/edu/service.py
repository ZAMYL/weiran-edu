"""Authorized staging and immutable batch lifecycle; no raw rows cross the DTO boundary."""

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.errors import APIError
from app.importing.edu import ParsedWorkbook, build_preview, build_quality_report, parse_excel
from app.importing.edu.preview import (
    Page,
    PreviewIssue,
    PreviewRow,
    PreviewStudent,
    PreviewSummary,
    summarize,
    validate_page,
)
from app.importing.edu.quality import QualityReport
from app.importing.edu.types import DIMENSIONS, PARSER_VERSION, Issue
from app.storage.database import Database

from .auth import Principal, accessible_batch, deny, require_scope, require_teacher
from .models import (
    Batch,
    BatchList,
    CommitRequest,
    CommitResult,
    ImportInfo,
    ImportMetadata,
    ImportPatch,
    ImportView,
    RecognizedImportItem,
)


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("UTC_CLOCK_REQUIRED")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def fail(code: str, status: int = 409) -> None:
    messages = {
        "IMPORT_EXPIRED": "预览已过期，请重新上传",
        "IMPORT_NOT_CONFIRMABLE": "该预览不能确认入库",
        "PREVIEW_VERSION_MISMATCH": "预览版本已变化，请刷新后重试",
        "WARNINGS_NOT_ACKNOWLEDGED": "请先复核并确认质量警告",
        "STUDENT_IDENTITY_CONFLICT": "学生身份映射需人工核对，请重新上传",
        "REVISION_CONFLICT": "修订目标已变化或不可修订",
        "IMPORT_CONFLICT": "导入与已有数据冲突",
        "DATA_INTEGRITY_ERROR": "数据完整性检查未通过",
        "SNAPSHOT_MISMATCH": "数据快照已变化，请重新读取",
    }
    raise APIError(status, code, messages[code])


def _version() -> str:
    return hashlib.sha256(uuid4().bytes + uuid4().bytes).hexdigest()


def _page(offset: int, limit: int, returned: int, total: int) -> Page:
    return Page(offset=offset, limit=limit, returned=returned, has_more=offset + returned < total)


def batch_dto(row: sqlite3.Row) -> Batch:
    # Explicit model field whitelist; never serialize owner columns or private source data.
    values = {key: row[key] for key in Batch.model_fields if key != "is_current"}
    return Batch(**values, is_current=bool(row["is_current"]))


class EduService:
    def __init__(
        self,
        database: Database,
        *,
        parser: Callable[[bytes], ParsedWorkbook] = parse_excel,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.database, self.parser, self.clock = database, parser, clock

    def _teacher(self, connection: sqlite3.Connection, principal: Principal) -> None:
        require_teacher(connection, principal, stamp(self.clock()))

    def _owned_import(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        import_id: str,
    ) -> sqlite3.Row:
        self._teacher(connection, principal)
        row = connection.execute(
            "SELECT * FROM imports WHERE import_id=? AND owner_teacher_id=?",
            (import_id, principal.teacher_id),
        ).fetchone()
        if row is None:
            deny(404)
        require_scope(connection, principal, row["data_scope_id"])
        return row

    def _pending(self, row: sqlite3.Row) -> None:
        if row["status"] == "committed":
            fail("IMPORT_NOT_CONFIRMABLE")
        if row["status"] == "expired" or row["expires_at"] <= stamp(self.clock()):
            fail("IMPORT_EXPIRED", 410)

    @staticmethod
    def _check_version(row: sqlite3.Row, version: str) -> None:
        if not hmac.compare_digest(row["preview_version"], version):
            fail("PREVIEW_VERSION_MISMATCH")

    @staticmethod
    def _revision(
        connection: sqlite3.Connection,
        principal: Principal,
        scope_id: str,
        revision_id: str | None,
    ) -> None:
        if revision_id is None:
            return
        row = accessible_batch(connection, principal, revision_id)
        if row["data_scope_id"] != scope_id or row["owner_teacher_id"] != principal.teacher_id:
            deny(404)
        if not row["is_current"]:
            fail("REVISION_CONFLICT")

    @staticmethod
    def _identity_conflicts(
        connection: sqlite3.Connection,
        scope_id: str,
        parsed: ParsedWorkbook,
    ) -> set[int]:
        conflicts = set()
        for student in parsed.students:
            if student.student_number is None:
                continue
            row = connection.execute(
                "SELECT student_name FROM student_identities "
                "WHERE data_scope_id=? AND student_number=?",
                (scope_id, student.student_number),
            ).fetchone()
            if row is not None and row["student_name"] != student.name:
                conflicts.add(student.header_row)
        return conflicts

    def create_import(
        self, principal: Principal, data: bytes, metadata: ImportMetadata
    ) -> ImportView:
        with self.database.transaction() as connection:
            self._teacher(connection, principal)
            require_scope(connection, principal, metadata.data_scope_id, importing=True)
            self._revision(
                connection, principal, metadata.data_scope_id, metadata.revision_of_batch_id
            )
        parsed = self.parser(data)  # CPU work stays outside the SQLite write transaction.
        with self.database.transaction() as connection:
            self._teacher(connection, principal)
            require_scope(connection, principal, metadata.data_scope_id, importing=True)
            self._revision(
                connection, principal, metadata.data_scope_id, metadata.revision_of_batch_id
            )
            conflicts = self._identity_conflicts(connection, metadata.data_scope_id, parsed)
            if conflicts:
                parsed = replace(
                    parsed,
                    content_sha256=None,
                    students=tuple(
                        replace(s, complete=False) if s.header_row in conflicts else s
                        for s in parsed.students
                    ),
                    issues=parsed.issues
                    + tuple(
                        Issue("STUDENT_IDENTITY_CONFLICT", "error", "Sheet2", f"A{row}", row)
                        for row in sorted(conflicts)
                    ),
                )
        report = build_quality_report(parsed)
        # Store only public projections as JSON. Raw input is a separate controlled BLOB.
        payload = {
            "rows": [
                r.model_dump(mode="json")
                for offset in range(0, report.summary.parsed_evaluation_count, 100)
                for r in build_preview(parsed, row_offset=offset, row_limit=100).rows
            ],
            "students": [
                s.model_dump(mode="json")
                for offset in range(0, report.summary.complete_student_count, 100)
                for s in build_preview(
                    parsed, student_offset=offset, student_limit=100
                ).student_samples
            ],
            "issues": [i.model_dump(mode="json") for i in report.issues],
        }
        rubric_version = None
        if not any(i.sheet_name == "Sheet1" and i.severity == "error" for i in parsed.issues):
            rubric_version = (
                "rubric.v1."
                + hashlib.sha256(
                    json.dumps(
                        [[r.dimension_code, r.title, r.text_cells] for r in parsed.rubrics],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            )
        with self.database.transaction(write=True) as connection:
            self._teacher(connection, principal)
            require_scope(connection, principal, metadata.data_scope_id, importing=True)
            self._revision(
                connection, principal, metadata.data_scope_id, metadata.revision_of_batch_id
            )
            created = self.clock()
            import_id = str(uuid4())
            connection.execute(
                "INSERT INTO imports (import_id,owner_teacher_id,data_scope_id,status,title,course,"
                "assessment_date,revision_of_batch_id,file_sha256,content_sha256,content_hash_version,"
                "parser_version,rubric_version,preview_version,created_at,expires_at,summary_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    principal.teacher_id,
                    metadata.data_scope_id,
                    "preview_ready" if parsed.can_commit else "invalid",
                    metadata.title,
                    metadata.course,
                    metadata.assessment_date,
                    metadata.revision_of_batch_id,
                    parsed.file_sha256,
                    parsed.content_sha256,
                    "content.v1",
                    PARSER_VERSION,
                    rubric_version,
                    _version(),
                    stamp(created),
                    stamp(created + timedelta(hours=24)),
                    json.dumps(summarize(parsed).model_dump()),
                ),
            )
            connection.execute(
                "INSERT INTO import_payloads VALUES (?,?,?,?)",
                (
                    import_id,
                    data,
                    json.dumps(payload, ensure_ascii=False),
                    report.model_dump_json(),
                ),
            )
        return self.get_import(principal, import_id)

    def get_import(
        self,
        principal: Principal,
        import_id: str,
        *,
        row_offset: int = 0,
        row_limit: int = 25,
        student_offset: int = 0,
        student_limit: int = 20,
        issue_offset: int = 0,
        issue_limit: int = 50,
    ) -> ImportView:
        for offset, limit in (
            (row_offset, row_limit),
            (student_offset, student_limit),
            (issue_offset, issue_limit),
        ):
            validate_page(offset, limit)
        with self.database.transaction() as connection:
            row = self._owned_import(connection, principal, import_id)
            info = ImportInfo(**{key: row[key] for key in ImportInfo.model_fields})
            summary = PreviewSummary.model_validate_json(row["summary_json"])
            if row["status"] == "committed":
                accessible_batch(connection, principal, row["committed_batch_id"])
                return ImportView(
                    import_info=info,
                    summary=summary,
                    preview_available=False,
                    recognized_items=None,
                    student_samples=None,
                    student_page=None,
                    rows=None,
                    row_page=None,
                    issues=None,
                    issue_page=None,
                )
            self._pending(row)
            stored = connection.execute(
                "SELECT preview_json,quality_json FROM import_payloads WHERE import_id=?",
                (import_id,),
            ).fetchone()
            if stored is None:
                fail("DATA_INTEGRITY_ERROR", 503)
            payload = json.loads(stored["preview_json"])
            quality = QualityReport.model_validate_json(stored["quality_json"])
            recognized_items = tuple(
                RecognizedImportItem(
                    item_id=dimension.dimension_code,
                    display_name=dimension.dimension_label,
                    score_row_count=dimension.score_row_count,
                )
                for dimension in quality.dimensions
                if dimension.score_row_count > 0
            )
            rows = tuple(
                PreviewRow.model_validate(r)
                for r in payload["rows"][row_offset : row_offset + row_limit]
            )
            students = tuple(
                PreviewStudent.model_validate(s)
                for s in payload["students"][student_offset : student_offset + student_limit]
            )
            issues = tuple(
                PreviewIssue.model_validate(i)
                for i in payload["issues"][issue_offset : issue_offset + issue_limit]
            )
            return ImportView(
                import_info=info,
                summary=summary,
                preview_available=True,
                recognized_items=recognized_items,
                rows=rows,
                row_page=_page(row_offset, row_limit, len(rows), len(payload["rows"])),
                student_samples=students,
                student_page=_page(
                    student_offset, student_limit, len(students), len(payload["students"])
                ),
                issues=issues,
                issue_page=_page(issue_offset, issue_limit, len(issues), len(payload["issues"])),
            )

    def quality_report(self, principal: Principal, import_id: str) -> QualityReport:
        with self.database.transaction() as connection:
            row = self._owned_import(connection, principal, import_id)
            if row["status"] == "committed":
                fail("IMPORT_NOT_CONFIRMABLE")
            self._pending(row)
            payload = connection.execute(
                "SELECT quality_json FROM import_payloads WHERE import_id=?", (import_id,)
            ).fetchone()
            if payload is None:
                fail("DATA_INTEGRITY_ERROR", 503)
            return QualityReport.model_validate_json(payload["quality_json"])

    def patch_import(self, principal: Principal, import_id: str, patch: ImportPatch) -> ImportView:
        with self.database.transaction(write=True) as connection:
            row = self._owned_import(connection, principal, import_id)
            self._check_version(row, patch.preview_version)
            self._pending(row)
            require_scope(connection, principal, row["data_scope_id"], importing=True)
            fields = patch.model_fields_set - {"preview_version"}
            connection.execute(
                "UPDATE imports SET title=?,course=?,assessment_date=?,preview_version=? "
                "WHERE import_id=?",
                tuple(
                    getattr(patch, field) if field in fields else row[field]
                    for field in ("title", "course", "assessment_date")
                )
                + (_version(), import_id),
            )
        return self.get_import(principal, import_id)

    def _commit_gate(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        row: sqlite3.Row,
        request: CommitRequest,
    ) -> CommitResult | None:
        self._check_version(row, request.preview_version)
        if row["status"] == "committed":
            batch = accessible_batch(connection, principal, row["committed_batch_id"])
            return CommitResult(
                import_id=row["import_id"],
                batch_id=row["committed_batch_id"],
                snapshot_id=batch["snapshot_id"],
                result="replayed",
            )
        self._pending(row)
        require_scope(connection, principal, row["data_scope_id"], importing=True)
        if row["status"] != "preview_ready":
            fail("IMPORT_NOT_CONFIRMABLE")
        if json.loads(row["summary_json"])["warning_count"] and not request.acknowledge_warnings:
            fail("WARNINGS_NOT_ACKNOWLEDGED")
        return None

    def commit(self, principal: Principal, import_id: str, request: CommitRequest) -> CommitResult:
        with self.database.transaction() as connection:
            row = self._owned_import(connection, principal, import_id)
            replay = self._commit_gate(connection, principal, row, request)
            if replay:
                return replay
            payload = connection.execute(
                "SELECT raw_xlsx FROM import_payloads WHERE import_id=?", (import_id,)
            ).fetchone()
            if payload is None:
                fail("DATA_INTEGRITY_ERROR", 503)
            data = payload["raw_xlsx"]
        parsed = self.parser(data)
        with self.database.transaction(write=True) as connection:
            row = self._owned_import(connection, principal, import_id)
            replay = self._commit_gate(connection, principal, row, request)
            if replay:
                return replay
            if (
                hashlib.sha256(data).hexdigest() != row["file_sha256"]
                or parsed.file_sha256 != row["file_sha256"]
                or parsed.content_sha256 != row["content_sha256"]
                or row["parser_version"] != PARSER_VERSION
                or not parsed.can_commit
            ):
                fail("DATA_INTEGRITY_ERROR", 503)
            if self._identity_conflicts(connection, row["data_scope_id"], parsed):
                fail("STUDENT_IDENTITY_CONFLICT")
            duplicate = connection.execute(
                "SELECT batch_id FROM batches WHERE data_scope_id=? AND (file_sha256=? OR "
                "(content_hash_version='content.v1' AND content_sha256=?))",
                (row["data_scope_id"], row["file_sha256"], row["content_sha256"]),
            ).fetchone()
            if duplicate:
                try:
                    accessible_batch(connection, principal, duplicate["batch_id"])
                except APIError as exc:
                    if exc.status_code != 404:
                        raise
                    fail("IMPORT_CONFLICT")
                batch_id, result = duplicate["batch_id"], "duplicate"
            else:
                self._revision(
                    connection, principal, row["data_scope_id"], row["revision_of_batch_id"]
                )
                batch_id = self._persist_batch(connection, row, parsed, data)
                result = "created"
            connection.execute(
                "UPDATE imports SET status='committed',committed_batch_id=? WHERE import_id=?",
                (batch_id, import_id),
            )
            connection.execute("DELETE FROM import_payloads WHERE import_id=?", (import_id,))
            batch = accessible_batch(connection, principal, batch_id)
            return CommitResult(
                import_id=import_id,
                batch_id=batch_id,
                snapshot_id=batch["snapshot_id"],
                result=result,
            )

    def _persist_batch(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        parsed: ParsedWorkbook,
        data: bytes,
    ) -> str:
        codes = {spec.code for spec in DIMENSIONS}
        if (
            not parsed.students
            or len(parsed.rubrics) != 5
            or any(
                not s.complete
                or len(s.evaluations) != 5
                or {e.dimension_code for e in s.evaluations} != codes
                for s in parsed.students
            )
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        now, batch_id, rubric_id = stamp(self.clock()), str(uuid4()), str(uuid4())
        connection.execute(
            "INSERT INTO rubric_snapshots VALUES (?,?,?)", (rubric_id, row["rubric_version"], now)
        )
        for rubric in parsed.rubrics:
            connection.execute(
                "INSERT INTO rubric_criteria VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    rubric_id,
                    rubric.dimension_code,
                    rubric.title,
                    json.dumps(rubric.text_cells, ensure_ascii=False),
                    row["file_sha256"],
                    "Sheet1",
                    rubric.source_range,
                ),
            )
        connection.execute(
            "INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                batch_id,
                str(uuid4()),
                row["data_scope_id"],
                row["owner_teacher_id"],
                row["import_id"],
                row["title"],
                row["course"],
                row["assessment_date"],
                rubric_id,
                row["file_sha256"],
                row["content_sha256"],
                "content.v1",
                row["parser_version"],
                row["revision_of_batch_id"],
                len(parsed.students),
                len(parsed.students) * 5,
                now,
            ),
        )
        connection.execute("INSERT INTO batch_sources VALUES (?,?)", (batch_id, data))
        for order, student in enumerate(parsed.students, 1):
            identity = connection.execute(
                "SELECT student_pk FROM student_identities "
                "WHERE data_scope_id=? AND student_number=?",
                (row["data_scope_id"], student.student_number),
            ).fetchone()
            student_pk = identity["student_pk"] if identity else str(uuid4())
            if identity is None:
                connection.execute(
                    "INSERT INTO students VALUES (?,?,?,?)",
                    (student_pk, str(uuid4()), row["data_scope_id"], now),
                )
                connection.execute(
                    "INSERT INTO student_identities VALUES (?,?,?,?,?)",
                    (student_pk, row["data_scope_id"], student.student_number, student.name, now),
                )
            connection.execute(
                "INSERT INTO batch_students VALUES (?,?,?,?,?)",
                (batch_id, student_pk, row["data_scope_id"], f"学生 {order:03d}", order),
            )
            for evaluation in student.evaluations:
                source = evaluation.source
                if source.file_sha256 != row["file_sha256"]:
                    fail("DATA_INTEGRITY_ERROR", 503)
                self._persist_evaluation(
                    connection,
                    (
                        str(uuid4()),
                        batch_id,
                        student_pk,
                        rubric_id,
                        evaluation.dimension_code,
                        evaluation.score_units,
                        str(evaluation.score_raw),
                        evaluation.comment_raw or "",
                        int(evaluation.comment_raw is None),
                        None,
                        "withheld"
                        if evaluation.comment_raw and evaluation.comment_raw.strip()
                        else "empty",
                        student.raw_identity,
                        source.file_sha256,
                        source.sheet_name,
                        source.row_number,
                        source.dimension_cell,
                        source.score_cell,
                        source.comment_cell,
                        now,
                    ),
                )
        actual = connection.execute(
            "SELECT COUNT(*) FROM evaluations WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        if actual != len(parsed.students) * 5:
            fail("DATA_INTEGRITY_ERROR", 503)
        return batch_id

    @staticmethod
    def _persist_evaluation(connection: sqlite3.Connection, values: tuple) -> None:
        connection.execute(
            "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values
        )

    def get_batch(self, principal: Principal, batch_id: str) -> Batch:
        with self.database.transaction() as connection:
            self._teacher(connection, principal)
            return batch_dto(accessible_batch(connection, principal, batch_id))

    def list_batches(
        self,
        principal: Principal,
        *,
        data_scope_id: str | None = None,
        include_revisions: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> BatchList:
        validate_page(offset, limit)
        with self.database.transaction() as connection:
            self._teacher(connection, principal)
            where = (
                " FROM batches b JOIN scope_members m ON m.data_scope_id=b.data_scope_id "
                "AND m.teacher_id=? WHERE (b.owner_teacher_id=? "
                "OR EXISTS(SELECT 1 FROM batch_access a "
                "WHERE a.batch_id=b.batch_id AND a.teacher_id=?)) "
                "AND (? IS NULL OR b.data_scope_id=?) AND (? OR NOT EXISTS(SELECT 1 "
                "FROM batches n WHERE n.revision_of_batch_id=b.batch_id))"
            )
            params = (principal.teacher_id,) * 3 + (
                data_scope_id,
                data_scope_id,
                int(include_revisions),
            )
            total = connection.execute("SELECT COUNT(*)" + where, params).fetchone()[0]
            ids = connection.execute(
                "SELECT b.batch_id"
                + where
                + " ORDER BY b.created_at DESC,b.batch_id DESC LIMIT ? OFFSET ?",
                params + (limit, offset),
            ).fetchall()
            items = tuple(
                batch_dto(accessible_batch(connection, principal, item["batch_id"])) for item in ids
            )
            return BatchList(
                total_count=total, items=items, page=_page(offset, limit, len(items), total)
            )

    def cleanup_expired(self) -> int:
        """Operator-only expiry cleanup; committed metadata remains replayable."""
        with self.database.transaction(write=True) as connection:
            now = stamp(self.clock())
            connection.execute(
                "DELETE FROM import_payloads WHERE import_id IN (SELECT import_id FROM imports "
                "WHERE status IN ('preview_ready','invalid') AND expires_at<=?)",
                (now,),
            )
            cursor = connection.execute(
                "UPDATE imports SET status='expired' WHERE status IN "
                "('preview_ready','invalid') AND expires_at<=?",
                (now,),
            )
            return cursor.rowcount
