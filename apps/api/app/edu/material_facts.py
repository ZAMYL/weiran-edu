"""B-02-S2: turn confirmed positions of a stored material into business facts.

Re-uploading the exact same bytes is the only proof we still have: the record keeps
the hash and the structure snapshot, never the cells. So ingestion is explicit, byte
verified, and it writes nothing unless the teacher already confirmed which column is
the participant, the score, the verdict, the dimension or the dimension score.

Identity linking uses only the scope's own student number: a fact is attached to an
existing student when the confirmed participant cell equals that number exactly, and
stays standalone otherwise. Nothing is joined by row order or by similar names.
"""

import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from app.ai.edu.material_contract import MaterialPlan
from app.core.errors import APIError
from app.edu.auth import Principal, require_scope, require_teacher
from app.edu.material_fact_models import (
    MaterialDimensionStatistic,
    MaterialFact,
    MaterialFactList,
    MaterialFactsIngest,
    MaterialFactStatistics,
    MaterialProblemStatistic,
)
from app.edu.service import _page
from app.storage.database import Database

RULE_VERSION = "material_facts.v1"
FILE_REF = re.compile(r"^F\d+\.")
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
STUDENT_NUMBER = re.compile(r"^\d{12}$")


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("UTC_CLOCK_REQUIRED")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _fail(status: int, code: str, message: str) -> None:
    raise APIError(status, code, message)


def _local(ref: str) -> str:
    """Stored refs carry the upload's file ordinal (F2.T1); a single re-upload is F1."""
    return FILE_REF.sub("F1.", ref, count=1)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


def _number(value: str | None) -> float | None:
    if value is None or not NUMBER.match(value):
        return None
    try:
        return float(Decimal(value))
    except InvalidOperation:
        return None


def _display(value: Decimal | None) -> str:
    if value is None:
        return "暂无有效数据"
    units = (value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{units / 100:.2f}"


class MaterialFactStore:
    """Confirmed positions -> facts; the same bytes again is the only way in."""

    def __init__(self, database: Database, *, clock: Callable[[], datetime] | None = None) -> None:
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> str:
        return _stamp(self.clock())

    # ---------------------------------------------------------------- ingest

    def check_bytes(self, principal: Principal, source_id: str, digest: str) -> sqlite3.Row:
        """Ownership and byte identity before any parsing happens."""
        with self.database.transaction() as connection:
            require_teacher(connection, principal, self._now())
            row = connection.execute(
                "SELECT * FROM material_records WHERE source_id=?", (source_id,)
            ).fetchone()
            if row is None:
                _fail(404, "MATERIAL_NOT_FOUND", "资料不存在")
            if row["owner_teacher_id"] != principal.teacher_id:
                _fail(403, "MATERIAL_OWNER_REQUIRED", "只有上传者可以把该资料写入业务事实")
            if row["status"] != "active":
                _fail(409, "MATERIAL_REVOKED", "该资料已撤回，不能再写入业务事实")
            if row["data_scope_id"] is None:
                _fail(409, "MATERIAL_SCOPE_REQUIRED", "资料尚未确认归属教学空间")
            if row["sha256"] != digest:
                _fail(409, "MATERIAL_CONTENT_MISMATCH", "内容与已登记的哈希不一致，不能复用确认")
            return row

    def ingest(
        self,
        principal: Principal,
        source_id: str,
        digest: str,
        document,
        plan: MaterialPlan,
    ) -> MaterialFactsIngest:
        now = self._now()
        with self.database.transaction(write=True) as connection:
            require_teacher(connection, principal, now)
            row = connection.execute(
                "SELECT * FROM material_records WHERE source_id=?", (source_id,)
            ).fetchone()
            if row is None or row["owner_teacher_id"] != principal.teacher_id:
                _fail(404, "MATERIAL_NOT_FOUND", "资料不存在")
            if row["status"] != "active":
                _fail(409, "MATERIAL_REVOKED", "该资料已撤回，不能再写入业务事实")
            if row["sha256"] != digest:
                _fail(409, "MATERIAL_CONTENT_MISMATCH", "内容与已登记的哈希不一致，不能复用确认")
            scope_id = row["data_scope_id"]
            if scope_id is None:
                _fail(409, "MATERIAL_SCOPE_REQUIRED", "资料尚未确认归属教学空间")
            if row["plan_sha256"] is None:
                _fail(409, "MATERIAL_PLAN_UNKNOWN", "登记的结构指纹缺失，不能写入业务事实")
            require_scope(connection, principal, scope_id, importing=True)
            latest = self._latest_confirmations(connection, row["material_id"])
            tables = self._confirmed_tables(latest, plan)
            if not tables:
                _fail(
                    409,
                    "MATERIAL_CONFIRMATION_REQUIRED",
                    "需要先确认参与者与成绩（或能力维度与评分）所在列",
                )
            rows_scanned = 0
            written = 0
            skipped = 0
            linked = 0
            connection.execute(
                "DELETE FROM material_facts WHERE material_id=?", (row["material_id"],)
            )
            for table_ref, fields in tables.items():
                table = document.document.tables[plan.locators[_local(table_ref)].table_index]
                indexes = {
                    meaning: plan.locators[_local(column)].column_index
                    for column, meaning in fields.items()
                }
                if any(index is None for index in indexes.values()):
                    _fail(409, "MATERIAL_STRUCTURE_CHANGED", "确认位置在当前文件中已不存在")
                for offset, cells in enumerate(table.rows):
                    rows_scanned += 1
                    participant = _text(self._cell(cells, indexes["student_key"]))
                    if participant is None:
                        skipped += 1
                        continue
                    student_pk, basis = self._identity(connection, scope_id, participant)
                    if student_pk is not None:
                        linked += 1
                    problem = _text(self._cell(cells, indexes.get("problem_key")))
                    score = _text(self._cell(cells, indexes.get("score")))
                    verdict = _text(self._cell(cells, indexes.get("verdict")))
                    dimension = _text(self._cell(cells, indexes.get("dimension")))
                    dimension_score = _text(self._cell(cells, indexes.get("dimension_score")))
                    feedback = _text(self._cell(cells, indexes.get("feedback")))
                    produced = 0
                    if score is not None or verdict is not None:
                        produced += self._insert(
                            connection,
                            row=row,
                            scope_id=scope_id,
                            now=now,
                            kind="score",
                            participant=participant,
                            student_pk=student_pk,
                            basis=basis,
                            problem=problem,
                            dimension=dimension,
                            score=score,
                            verdict=verdict,
                            dimension_score=None,
                            feedback=feedback,
                            table_ref=table_ref,
                            source_row=offset + 1,
                        )
                    if dimension is not None and dimension_score is not None:
                        produced += self._insert(
                            connection,
                            row=row,
                            scope_id=scope_id,
                            now=now,
                            kind="dimension",
                            participant=participant,
                            student_pk=student_pk,
                            basis=basis,
                            problem=problem,
                            dimension=dimension,
                            score=None,
                            verdict=verdict,
                            dimension_score=dimension_score,
                            feedback=feedback,
                            table_ref=table_ref,
                            source_row=offset + 1,
                        )
                    if produced == 0:
                        skipped += 1
                    written += produced
            return MaterialFactsIngest(
                material_id=row["material_id"],
                source_id=row["source_id"],
                plan_sha256=row["plan_sha256"],
                file_sha256=row["sha256"],
                rows_scanned=rows_scanned,
                facts_written=written,
                linked_count=linked,
                skipped_without_participant=skipped,
            )

    @staticmethod
    def _cell(cells: Sequence[Any], index: int | None):
        if index is None or index >= len(cells):
            return None
        return cells[index]

    @staticmethod
    def _latest_confirmations(
        connection: sqlite3.Connection, material_id: str
    ) -> dict[tuple[str, str, str], sqlite3.Row]:
        rows = connection.execute(
            "SELECT kind,table_ref,column_ref,value,confirmed_at,confirmation_id "
            "FROM material_confirmations WHERE material_id=? AND stale=0 "
            "ORDER BY confirmed_at, confirmation_id",
            (material_id,),
        ).fetchall()
        latest: dict[tuple[str, str, str], sqlite3.Row] = {}
        for item in rows:
            latest[(item["kind"], item["table_ref"], item["column_ref"] or "")] = item
        return latest

    @staticmethod
    def _confirmed_tables(
        latest: dict[tuple[str, str, str], sqlite3.Row], plan: MaterialPlan
    ) -> dict[str, dict[str, str]]:
        """Only tables that already carry a confirmed participant and something to record."""
        grouped: dict[str, dict[str, str]] = {}
        for (kind, table_ref, column_ref), item in latest.items():
            fields = grouped.setdefault(table_ref, {})
            if kind == "field":
                fields[column_ref] = item["value"]
        result: dict[str, dict[str, str]] = {}
        for table_ref, fields in grouped.items():
            if "student_key" not in fields.values():
                continue
            usable = {
                column: meaning
                for column, meaning in fields.items()
                if meaning
                in {
                    "student_key",
                    "problem_key",
                    "score",
                    "verdict",
                    "dimension",
                    "dimension_score",
                    "feedback",
                }
            }
            if not {"score", "verdict", "dimension_score"} & set(usable.values()):
                continue
            if table_ref not in plan.locators and _local(table_ref) not in plan.locators:
                continue
            for column in usable:
                if column not in plan.locators and _local(column) not in plan.locators:
                    break
            else:
                result[table_ref] = usable
        return result

    @staticmethod
    def _identity(
        connection: sqlite3.Connection, scope_id: str, participant: str
    ) -> tuple[str | None, str]:
        if not STUDENT_NUMBER.match(participant):
            return None, "none"
        row = connection.execute(
            "SELECT student_pk FROM student_identities WHERE data_scope_id=? AND student_number=?",
            (scope_id, participant),
        ).fetchone()
        return (row["student_pk"], "student_number") if row else (None, "none")

    def _insert(
        self,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        scope_id: str,
        now: str,
        kind: str,
        participant: str,
        student_pk: str | None,
        basis: str,
        problem: str | None,
        dimension: str | None,
        score: str | None,
        verdict: str | None,
        dimension_score: str | None,
        feedback: str | None,
        table_ref: str,
        source_row: int,
    ) -> int:
        connection.execute(
            "INSERT INTO material_facts (fact_id,material_id,data_scope_id,owner_teacher_id,"
            "kind,participant_key,student_pk,identity_basis,problem_key,dimension,score_raw,"
            "score_value,verdict,dimension_score_raw,dimension_score_value,feedback,"
            "source_table,source_row,plan_sha256,file_sha256,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                row["material_id"],
                scope_id,
                row["owner_teacher_id"],
                kind,
                participant,
                student_pk,
                basis,
                problem,
                dimension,
                score,
                _number(score),
                verdict,
                dimension_score,
                _number(dimension_score),
                feedback,
                table_ref,
                source_row,
                row["plan_sha256"],
                row["sha256"],
                now,
            ),
        )
        return 1

    # ------------------------------------------------------------ read paths

    def _visible(
        self, connection: sqlite3.Connection, principal: Principal, source_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM material_records WHERE source_id=?", (source_id,)
        ).fetchone()
        if row is None:
            _fail(404, "MATERIAL_NOT_FOUND", "资料不存在")
        if row["owner_teacher_id"] == principal.teacher_id:
            return row
        scope_id = row["data_scope_id"]
        if (
            scope_id is not None
            and connection.execute(
                "SELECT 1 FROM scope_members WHERE data_scope_id=? AND teacher_id=?",
                (scope_id, principal.teacher_id),
            ).fetchone()
        ):
            return row
        _fail(404, "MATERIAL_NOT_FOUND", "资料不存在")

    def list(
        self,
        principal: Principal,
        source_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> MaterialFactList:
        with self.database.transaction() as connection:
            require_teacher(connection, principal, self._now())
            record = self._visible(connection, principal, source_id)
            total = connection.execute(
                "SELECT COUNT(*) FROM material_facts WHERE material_id=?",
                (record["material_id"],),
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT f.*, s.public_id FROM material_facts f "
                "LEFT JOIN students s ON s.student_pk=f.student_pk "
                "WHERE f.material_id=? ORDER BY f.source_table, f.source_row, f.kind, f.fact_id "
                "LIMIT ? OFFSET ?",
                (record["material_id"], limit, offset),
            ).fetchall()
            return MaterialFactList(
                material_id=record["material_id"],
                source_id=record["source_id"],
                plan_sha256=record["plan_sha256"],
                rule_version=RULE_VERSION,
                total_count=total,
                items=[self._fact(item) for item in rows],
                page=_page(offset, limit, len(rows), total),
            )

    @staticmethod
    def _fact(row: sqlite3.Row) -> MaterialFact:
        return MaterialFact(
            fact_id=row["fact_id"],
            material_id=row["material_id"],
            kind=row["kind"],
            participant_key=row["participant_key"],
            student_id=row["public_id"],
            identity_basis=row["identity_basis"],
            problem_key=row["problem_key"],
            dimension=row["dimension"],
            score=row["score_raw"],
            score_value=row["score_value"],
            verdict=row["verdict"],
            dimension_score=row["dimension_score_raw"],
            dimension_score_value=row["dimension_score_value"],
            feedback=row["feedback"],
            source_table=row["source_table"],
            source_row=row["source_row"],
            created_at=row["created_at"],
        )

    def statistics(self, principal: Principal, source_id: str) -> MaterialFactStatistics:
        with self.database.transaction() as connection:
            require_teacher(connection, principal, self._now())
            record = self._visible(connection, principal, source_id)
            rows = connection.execute(
                "SELECT * FROM material_facts WHERE material_id=?", (record["material_id"],)
            ).fetchall()
        participants = {(item["participant_key"]) for item in rows}
        linked = {item["participant_key"] for item in rows if item["student_pk"] is not None}
        problems: dict[str | None, list[sqlite3.Row]] = {}
        dimensions: dict[str, list[sqlite3.Row]] = {}
        for item in rows:
            if item["kind"] == "score":
                problems.setdefault(item["problem_key"], []).append(item)
            elif item["dimension"] is not None:
                dimensions.setdefault(item["dimension"], []).append(item)
        return MaterialFactStatistics(
            material_id=record["material_id"],
            source_id=record["source_id"],
            plan_sha256=record["plan_sha256"],
            rule_version=RULE_VERSION,
            fact_count=len(rows),
            participant_count=len(participants),
            linked_participant_count=len(linked),
            problems=[
                self._problem(key, items)
                for key, items in sorted(
                    problems.items(), key=lambda pair: (pair[0] is None, pair[0] or "")
                )
            ],
            dimensions=[
                self._dimension(key, items)
                for key, items in sorted(dimensions.items(), key=lambda pair: pair[0])
            ],
        )

    @staticmethod
    def _values(items: Sequence[sqlite3.Row], column: str) -> Sequence[Decimal]:
        return [Decimal(str(item[column])) for item in items if item[column] is not None]

    def _problem(self, key: str | None, items: Sequence[sqlite3.Row]) -> MaterialProblemStatistic:
        scores = self._values(items, "score_value")
        verdicts = Counter(item["verdict"] for item in items if item["verdict"] is not None)
        mean = sum(scores) / len(scores) if scores else None
        return MaterialProblemStatistic(
            problem_key=key,
            fact_count=len(items),
            scored_count=len(scores),
            mean=_display(mean),
            minimum=_display(min(scores) if scores else None),
            maximum=_display(max(scores) if scores else None),
            verdicts=dict(sorted(verdicts.items())),
        )

    def _dimension(self, key: str, items: Sequence[sqlite3.Row]) -> MaterialDimensionStatistic:
        scores = self._values(items, "dimension_score_value")
        mean = sum(scores) / len(scores) if scores else None
        return MaterialDimensionStatistic(
            dimension=key,
            fact_count=len(items),
            scored_count=len(scores),
            mean=_display(mean),
            minimum=_display(min(scores) if scores else None),
            maximum=_display(max(scores) if scores else None),
        )
