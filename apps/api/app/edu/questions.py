"""Authorized thirteen-question slice using existing teacher/scope credentials."""

import hmac
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import timedelta
from fractions import Fraction
from hashlib import sha256
from uuid import uuid4

from app.core.errors import APIError
from app.importing.edu.preview import Page, validate_page
from app.importing.edu.questions import PROTOCOL, QuestionWorkbook, parse_questions
from app.importing.edu.worker import parse_isolated

from .analysis import stat
from .auth import Principal, accessible_batch, deny, require_scope, require_teacher
from .models import Batch, CommitRequest, CommitResult, ImportMetadata, ImportPatch, Model
from .service import EduService, _page, batch_dto, fail, stamp


class QuestionPreview(Model):
    format: str = PROTOCOL
    import_id: str
    preview_version: str
    title: str
    participant_count: int
    observation_count: int
    recognized_items: tuple[dict, ...]
    available_analyses: tuple[str, ...]
    quality_counts: dict[str, int]
    quality_issues: tuple[dict, ...]
    confirmable: bool = True


class QuestionStatistics(Model):
    format: str = PROTOCOL
    batch_id: str
    snapshot_id: str
    title: str
    participant_count: int
    recognized_items: tuple[dict, ...]
    available_analyses: tuple[str, ...]
    item_statistics: tuple[dict, ...]
    items: tuple[dict, ...]
    analysis_status: dict


class QuestionEvidence(Model):
    format: str = PROTOCOL
    batch_id: str
    snapshot_id: str
    evidence_id: str
    participant_id: str
    item_id: str
    quality_state: str
    exact: dict | None
    source: dict


class QuestionBatchInfo(Model):
    format: str = PROTOCOL
    batch_id: str
    snapshot_id: str
    data_scope_id: str
    title: str
    course: str | None
    assessment_date: str | None
    student_count: int
    evaluation_count: int
    recognized_items: tuple[dict, ...]
    available_analyses: tuple[str, ...]


class MixedBatchList(Model):
    total_count: int
    items: tuple[Batch | QuestionBatchInfo, ...]
    page: Page


def exact(value: Fraction | None) -> dict | None:
    return (
        None if value is None else {"numerator": value.numerator, "denominator": value.denominator}
    )


def recognized(parsed: QuestionWorkbook) -> tuple[dict, ...]:
    return tuple(
        {
            **asdict(i),
            "display_name": i.original_name,
            "scale": {"minimum": i.minimum, "maximum": i.maximum, "step": i.step, "unit": i.unit},
        }
        for i in parsed.questions
    )


class QuestionService:
    def __init__(self, backend: EduService) -> None:
        self.backend = backend
        self.database = backend.database

    def contains(self, identifier: str, *, batch: bool = False) -> bool:
        table, key = (
            ("question_batches", "batch_id") if batch else ("question_imports", "import_id")
        )
        with self.database.transaction() as c:
            return (
                c.execute(f"SELECT 1 FROM {table} WHERE {key}=?", (identifier,)).fetchone()
                is not None
            )

    def _actor(self, c: sqlite3.Connection, actor: Principal) -> None:
        require_teacher(c, actor, stamp(self.backend.clock()))

    def list_batches(
        self,
        actor: Principal,
        *,
        data_scope_id: str | None = None,
        include_revisions: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> MixedBatchList:
        validate_page(offset, limit)
        with self.database.transaction() as c:
            self._actor(c, actor)
            legacy = (
                "SELECT b.batch_id,0 AS dynamic,julianday(b.created_at) AS ordering FROM batches b "
                "JOIN scope_members m ON m.data_scope_id=b.data_scope_id AND m.teacher_id=? "
                "WHERE (b.owner_teacher_id=? OR EXISTS(SELECT 1 FROM batch_access a WHERE "
                "a.batch_id=b.batch_id AND a.teacher_id=?)) AND (? IS NULL OR b.data_scope_id=?) "
                "AND (? OR NOT EXISTS(SELECT 1 FROM batches n "
                "WHERE n.revision_of_batch_id=b.batch_id))"
            )
            dynamic = (
                "SELECT b.batch_id,1 AS dynamic,julianday(i.expires_at)-1.0/24 AS ordering "
                "FROM question_batches b JOIN question_imports i ON i.import_id=b.source_import_id "
                "JOIN scope_members m ON m.data_scope_id=i.data_scope_id AND m.teacher_id=? "
                "WHERE (i.owner_teacher_id=? OR EXISTS(SELECT 1 FROM question_access a WHERE "
                "a.batch_id=b.batch_id AND a.teacher_id=?)) AND (? IS NULL OR i.data_scope_id=?)"
            )
            params = (actor.teacher_id,) * 3 + (
                data_scope_id,
                data_scope_id,
                int(include_revisions),
            )
            params += (actor.teacher_id,) * 3 + (data_scope_id, data_scope_id)
            union = f"({legacy} UNION ALL {dynamic})"
            total = c.execute("SELECT count(*) FROM " + union, params).fetchone()[0]
            rows = c.execute(
                "SELECT * FROM " + union + " ORDER BY ordering DESC,batch_id DESC LIMIT ? OFFSET ?",
                params + (limit, offset),
            ).fetchall()
            items = []
            for entry in rows:
                if not entry["dynamic"]:
                    items.append(batch_dto(accessible_batch(c, actor, entry["batch_id"])))
                    continue
                row, parsed, _ = self._batch(c, actor, entry["batch_id"], None)
                items.append(
                    QuestionBatchInfo(
                        batch_id=entry["batch_id"],
                        snapshot_id=row["snapshot_id"],
                        data_scope_id=row["data_scope_id"],
                        title=row["title"],
                        course=row["course"],
                        assessment_date=row["assessment_date"],
                        student_count=len(parsed.participants),
                        evaluation_count=len(parsed.observations),
                        recognized_items=recognized(parsed),
                        available_analyses=("item_statistics",)
                        if any(o.state == "valid" for o in parsed.observations)
                        else (),
                    )
                )
            return MixedBatchList(
                total_count=total, items=tuple(items), page=_page(offset, limit, len(items), total)
            )

    def _owned(self, c: sqlite3.Connection, actor: Principal, identifier: str) -> sqlite3.Row:
        self._actor(c, actor)
        row = c.execute(
            "SELECT * FROM question_imports WHERE import_id=? AND owner_teacher_id=?",
            (identifier, actor.teacher_id),
        ).fetchone()
        if row is None:
            deny(404)
        require_scope(c, actor, row["data_scope_id"], importing=True)
        return row

    def _version(self, row: sqlite3.Row, version: str) -> None:
        if not hmac.compare_digest(row["preview_version"], version):
            fail("PREVIEW_VERSION_MISMATCH")
        if row["expires_at"] <= stamp(self.backend.clock()):
            fail("IMPORT_EXPIRED", 410)

    def _parsed(self, row: sqlite3.Row) -> QuestionWorkbook:
        raw = row["raw_xlsx"]
        if sha256(raw).hexdigest() != row["file_sha256"]:
            fail("DATA_INTEGRITY_ERROR", 503)
        try:
            return parse_questions(raw)
        except ValueError:
            fail("DATA_INTEGRITY_ERROR", 503)

    def _view(self, row: sqlite3.Row, parsed: QuestionWorkbook) -> QuestionPreview:
        issues = tuple(
            {
                "category": o.state,
                "count": 1,
                "sheet": "Sheet2",
                "cell": o.source_cell,
                "item_id": o.item_id,
                "severity": "warning",
            }
            for o in parsed.observations
            if o.state != "valid"
        )
        return QuestionPreview(
            import_id=row["import_id"],
            preview_version=row["preview_version"],
            title=row["title"],
            participant_count=len(parsed.participants),
            observation_count=len(parsed.observations),
            recognized_items=recognized(parsed),
            available_analyses=("item_statistics",)
            if any(o.state == "valid" for o in parsed.observations)
            else (),
            quality_counts=dict(Counter(o.state for o in parsed.observations)),
            quality_issues=issues,
        )

    def create(self, actor: Principal, data: bytes, metadata: ImportMetadata) -> QuestionPreview:
        with self.database.transaction() as c:
            self._actor(c, actor)
            require_scope(c, actor, metadata.data_scope_id, importing=True)
        if metadata.revision_of_batch_id is not None:
            raise APIError(422, "QUESTION_REVISION_UNSUPPORTED", "逐题测评暂不支持修订")
        parsed = parse_isolated(data, questions=True)
        assert isinstance(parsed, QuestionWorkbook)
        identifier, version = str(uuid4()), sha256(uuid4().bytes).hexdigest()
        with self.database.transaction(write=True) as c:
            self._actor(c, actor)
            require_scope(c, actor, metadata.data_scope_id, importing=True)
            c.execute(
                "INSERT INTO question_imports VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    identifier,
                    actor.teacher_id,
                    metadata.data_scope_id,
                    metadata.title,
                    metadata.course,
                    metadata.assessment_date,
                    version,
                    stamp(self.backend.clock() + timedelta(hours=1)),
                    parsed.file_sha256,
                    data,
                ),
            )
            return self._view(self._owned(c, actor, identifier), parsed)

    def get(self, actor: Principal, identifier: str) -> QuestionPreview:
        with self.database.transaction() as c:
            row = self._owned(c, actor, identifier)
            self._version(row, row["preview_version"])
            return self._view(row, self._parsed(row))

    def patch(self, actor: Principal, identifier: str, patch: ImportPatch) -> QuestionPreview:
        with self.database.transaction(write=True) as c:
            row = self._owned(c, actor, identifier)
            self._version(row, patch.preview_version)
            if row["committed_batch_id"]:
                fail("IMPORT_NOT_CONFIRMABLE")
            values = {k: row[k] for k in ("title", "course", "assessment_date")}
            values.update(patch.model_dump(exclude_unset=True, exclude={"preview_version"}))
            c.execute(
                "UPDATE question_imports SET title=?,course=?,assessment_date=?,preview_version=? "
                "WHERE import_id=?",
                (*values.values(), sha256(uuid4().bytes).hexdigest(), identifier),
            )
            return self._view(self._owned(c, actor, identifier), self._parsed(row))

    def commit(self, actor: Principal, identifier: str, body: CommitRequest) -> CommitResult:
        with self.database.transaction(write=True) as c:
            row = self._owned(c, actor, identifier)
            self._version(row, body.preview_version)
            parsed = self._parsed(row)
            if (
                any(o.state != "valid" for o in parsed.observations)
                and not body.acknowledge_warnings
            ):
                fail("WARNINGS_NOT_ACKNOWLEDGED")
            batch = c.execute(
                "SELECT b.* FROM question_batches b JOIN question_imports i "
                "ON i.import_id=b.source_import_id WHERE i.data_scope_id=? "
                "AND i.owner_teacher_id=? AND i.file_sha256=?",
                (row["data_scope_id"], actor.teacher_id, row["file_sha256"]),
            ).fetchone()
            result = (
                "replayed" if row["committed_batch_id"] else "duplicate" if batch else "created"
            )
            if batch is None:
                batch_id = str(uuid4())
                snapshot = sha256((batch_id + parsed.file_sha256 + PROTOCOL).encode()).hexdigest()
                c.execute(
                    "INSERT INTO question_batches VALUES (?,?,?)", (batch_id, snapshot, identifier)
                )
                for item in parsed.questions:
                    c.execute(
                        "INSERT INTO question_items VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            batch_id,
                            item.item_id,
                            item.original_name,
                            "question",
                            item.minimum,
                            item.maximum,
                            item.step,
                            item.unit,
                            item.source_cell,
                        ),
                    )
                for o in parsed.observations:
                    c.execute(
                        "INSERT INTO question_observations VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            sha256(
                                f"{batch_id}:{o.participant_id}:{o.item_id}".encode()
                            ).hexdigest(),
                            batch_id,
                            o.participant_id,
                            o.item_id,
                            o.raw,
                            o.state,
                            str(o.exact.numerator) if o.exact is not None else None,
                            str(o.exact.denominator) if o.exact is not None else None,
                            o.source_cell,
                        ),
                    )
            else:
                batch_id, snapshot = batch["batch_id"], batch["snapshot_id"]
            c.execute(
                "UPDATE question_imports SET committed_batch_id=? WHERE import_id=?",
                (batch_id, identifier),
            )
            return CommitResult(
                import_id=identifier, batch_id=batch_id, snapshot_id=snapshot, result=result
            )

    def _batch(
        self, c: sqlite3.Connection, actor: Principal, identifier: str, snapshot: str | None
    ) -> tuple[sqlite3.Row, QuestionWorkbook, list[sqlite3.Row]]:
        self._actor(c, actor)
        row = c.execute(
            "SELECT b.*,i.* FROM question_batches b JOIN question_imports i "
            "ON i.import_id=b.source_import_id WHERE b.batch_id=? AND "
            "(i.owner_teacher_id=? OR EXISTS (SELECT 1 FROM question_access a "
            "WHERE a.batch_id=b.batch_id AND a.teacher_id=?))",
            (identifier, actor.teacher_id, actor.teacher_id),
        ).fetchone()
        if row is None:
            deny(404)
        require_scope(c, actor, row["data_scope_id"])
        if snapshot is not None and snapshot != row["snapshot_id"]:
            fail("SNAPSHOT_MISMATCH")
        parsed = self._parsed(row)
        expected = sha256((identifier + parsed.file_sha256 + PROTOCOL).encode()).hexdigest()
        items = c.execute(
            "SELECT * FROM question_items WHERE batch_id=? ORDER BY item_id", (identifier,)
        ).fetchall()
        observations = c.execute(
            "SELECT * FROM question_observations WHERE batch_id=? ORDER BY participant_id,item_id",
            (identifier,),
        ).fetchall()
        item_values = [
            (
                i.item_id,
                i.original_name,
                i.kind,
                i.minimum,
                i.maximum,
                i.step,
                i.unit,
                i.source_cell,
            )
            for i in parsed.questions
        ]
        actual_items = [
            tuple(
                i[k]
                for k in (
                    "item_id",
                    "original_name",
                    "kind",
                    "minimum",
                    "maximum",
                    "step",
                    "unit",
                    "source_range",
                )
            )
            for i in items
        ]
        expected_obs = [
            (
                o.participant_id,
                o.item_id,
                o.raw,
                o.state,
                str(o.exact.numerator) if o.exact is not None else None,
                str(o.exact.denominator) if o.exact is not None else None,
                o.source_cell,
            )
            for o in parsed.observations
        ]
        actual_obs = [
            tuple(
                o[k]
                for k in (
                    "participant_id",
                    "item_id",
                    "raw_value",
                    "quality_state",
                    "numerator",
                    "denominator",
                    "source_cell",
                )
            )
            for o in observations
        ]
        expected_obs.sort(key=lambda o: (o[0], o[1]))
        valid_ids = all(
            o["evidence_id"]
            == sha256(f"{identifier}:{o['participant_id']}:{o['item_id']}".encode()).hexdigest()
            for o in observations
        )
        if (
            not valid_ids
            or expected != row["snapshot_id"]
            or actual_items != item_values
            or actual_obs != expected_obs
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        return row, parsed, observations

    def statistics(
        self,
        actor: Principal,
        identifier: str,
        snapshot: str | None = None,
        item_id: str | None = None,
    ) -> QuestionStatistics:
        with self.database.transaction() as c:
            row, parsed, observations = self._batch(c, actor, identifier, snapshot)
            if item_id is not None and item_id not in {i.item_id for i in parsed.questions}:
                deny(404)
            items = []
            canonical = []
            for item in parsed.questions:
                if item_id is not None and item.item_id != item_id:
                    continue
                group = [o for o in parsed.observations if o.item_id == item.item_id]
                counts = Counter(o.state for o in group)
                valid = [o.exact for o in group if o.exact is not None]
                distribution = [
                    {
                        "score": float(score),
                        "exact": exact(score),
                        "count": count,
                        "denominator": len(parsed.participants),
                        "ratio": stat(Fraction(count, len(parsed.participants))).model_dump(),
                    }
                    for score, count in sorted(Counter(valid).items())
                ]
                mean = sum(valid, Fraction()) / len(valid) if valid else None
                ordered = sorted(valid)
                middle = len(ordered) // 2
                median = (
                    (
                        ordered[middle]
                        if len(ordered) % 2
                        else (ordered[middle - 1] + ordered[middle]) / 2
                    )
                    if ordered
                    else None
                )
                items.append(
                    {
                        **asdict(item),
                        "denominator": len(parsed.participants),
                        "valid_count": counts["valid"],
                        "missing_count": counts["missing"],
                        "invalid_count": len(group) - counts["valid"] - counts["missing"],
                        "quality_counts": dict(counts),
                        "statistics_status": "available" if valid else "unavailable",
                        "mean": exact(sum(valid, Fraction()) / len(valid)) if valid else None,
                        "distribution": distribution,
                        "evidence_ids": [
                            o["evidence_id"] for o in observations if o["item_id"] == item.item_id
                        ],
                    }
                )
                canonical.append(
                    {
                        **items[-1],
                        "display_name": item.original_name,
                        "scale": {
                            "minimum": item.minimum,
                            "maximum": item.maximum,
                            "step": item.step,
                            "unit": item.unit,
                        },
                        "mean": stat(mean).model_dump(),
                        "median": stat(median).model_dump(),
                        "minimum": stat(min(valid) if valid else None).model_dump(),
                        "maximum": stat(max(valid) if valid else None).model_dump(),
                    }
                )
            return QuestionStatistics(
                batch_id=identifier,
                snapshot_id=row["snapshot_id"],
                title=row["title"],
                participant_count=len(parsed.participants),
                recognized_items=recognized(parsed),
                available_analyses=("item_statistics",)
                if any(o.state == "valid" for o in parsed.observations)
                else (),
                item_statistics=tuple(canonical),
                items=tuple(items),
                analysis_status={
                    "item_statistics": "available"
                    if any(o.exact is not None for o in parsed.observations)
                    else "unavailable",
                    "five_dimension_discovery": "unsupported",
                    "evaluation_review": "missing_rubric_and_response",
                    "cross_assessment_growth": "unsupported",
                },
            )

    def evidence(
        self, actor: Principal, identifier: str, evidence_id: str, snapshot: str | None = None
    ) -> QuestionEvidence:
        with self.database.transaction() as c:
            row, _, observations = self._batch(c, actor, identifier, snapshot)
            o = next((o for o in observations if o["evidence_id"] == evidence_id), None)
            if o is None:
                deny(404)
            value = Fraction(int(o["numerator"]), int(o["denominator"])) if o["numerator"] else None
            return QuestionEvidence(
                batch_id=identifier,
                snapshot_id=row["snapshot_id"],
                evidence_id=evidence_id,
                participant_id=o["participant_id"],
                item_id=o["item_id"],
                quality_state=o["quality_state"],
                exact=exact(value),
                source={
                    "file_sha256": row["file_sha256"],
                    "sheet": "Sheet2",
                    "cell": o["source_cell"],
                },
            )
