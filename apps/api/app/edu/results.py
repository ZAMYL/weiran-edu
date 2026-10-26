"""Server-computed immutable analysis snapshots and versioned teacher judgments."""

import hashlib
import json
import sqlite3
from fractions import Fraction
from uuid import uuid4

from pydantic import ValidationError

from app.analysis.edu.engine import select_students
from app.analysis.edu.models import Condition
from app.core.errors import APIError
from app.importing.edu.preview import validate_page

from .analysis import DIMENSION_MAP, OPERATOR_MAP, stat
from .auth import Principal, deny
from .discovery_bridge import DiscoveryInput, DiscoveryInputReader
from .insight_models import InsightSpec
from .insights import InsightReader, card_view
from .privacy import checked_text
from .reads import BatchReader
from .review_models import (
    AnalysisCreate,
    AnalysisList,
    AnalysisPatch,
    AnalysisResult,
    AnalysisSnapshot,
    AnalysisSummary,
    FindingDecision,
    SavedDiscoveryFinding,
    SavedFinding,
)
from .reviews import require_owner, version_conflict
from .service import _page, fail, stamp

MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


class ResultService(BatchReader):
    @staticmethod
    def _rows(
        connection: sqlite3.Connection, batch_id: str, result_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        row = connection.execute(
            "SELECT * FROM analysis_results WHERE batch_id=? AND result_id=?", (batch_id, result_id)
        ).fetchone()
        if row is None:
            deny(404)
        version = connection.execute(
            "SELECT * FROM analysis_result_versions WHERE result_id=? "
            "ORDER BY version DESC LIMIT 1",
            (result_id,),
        ).fetchone()
        if version is None:
            fail("DATA_INTEGRITY_ERROR", 503)
        return row, version

    @staticmethod
    def _summary(row: sqlite3.Row, version: sqlite3.Row) -> AnalysisSummary:
        return AnalysisSummary(
            result_id=row["result_id"],
            batch_id=row["batch_id"],
            snapshot_id=row["snapshot_id"],
            version=version["version"],
            title=version["title"],
            created_at=row["created_at"],
            updated_at=version["created_at"],
        )

    @classmethod
    def _dto(cls, row: sqlite3.Row, version: sqlite3.Row) -> AnalysisResult:
        if hashlib.sha256(row["payload_json"].encode()).hexdigest() != row["payload_sha256"]:
            fail("DATA_INTEGRITY_ERROR", 503)
        try:
            snapshot = AnalysisSnapshot.model_validate_json(row["payload_json"])
            decisions = tuple(
                FindingDecision.model_validate(d) for d in json.loads(version["decisions_json"])
            )
        except (ValueError, ValidationError, TypeError):
            fail("DATA_INTEGRITY_ERROR", 503)
        by_id = {decision.candidate_id: decision for decision in decisions}
        ids = {card.candidate_id for card in snapshot.candidates}
        discovery = snapshot.discovery
        discovery_ids = {f.discovery_id for f in discovery.findings} if discovery else set()
        ids |= discovery_ids
        if (
            len(by_id) != len(decisions)
            or set(by_id) - ids
            or snapshot.query.snapshot_id != row["snapshot_id"]
            or snapshot.statistics.batch_id != row["batch_id"]
            or snapshot.statistics.snapshot_id != row["snapshot_id"]
            or any(
                c.batch_id != row["batch_id"] or c.snapshot_id != row["snapshot_id"]
                for c in snapshot.candidates
            )
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        if discovery is not None and (
            snapshot.candidates
            or snapshot.query.include_dimensions
            or len(discovery.findings) > 3
            or len(discovery_ids) != len(discovery.findings)
            or discovery.spec.snapshot_id != row["snapshot_id"]
            or set(discovery.spec.scope_conditions) != set(snapshot.query.conditions)
            or discovery.scope.batch_id != row["batch_id"]
            or discovery.scope.snapshot_id != row["snapshot_id"]
            or discovery.scope.scope_record_count != snapshot.matched_count
            or discovery.scope.batch_record_count != snapshot.total_count
            or discovery.scope.scope_conditions != discovery.spec.scope_conditions
            or discovery.scope.statistics != (snapshot.group_statistics or snapshot.statistics)
            or any(
                f.scope != discovery.scope
                or f.input_key != discovery.scope.input_key
                or f.batch_id != row["batch_id"]
                or f.snapshot_id != row["snapshot_id"]
                for f in discovery.findings
            )
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        return AnalysisResult(
            **cls._summary(row, version).model_dump(),
            note=version["teacher_note"],
            reviewed_by=version["reviewed_by"],
            analysis=snapshot,
            findings=tuple(
                SavedFinding(
                    candidate=card,
                    teacher_review=by_id.get(card.candidate_id)
                    or FindingDecision(candidate_id=card.candidate_id, status="pending"),
                )
                for card in snapshot.candidates
            ),
            discovery_findings=tuple(
                SavedDiscoveryFinding(
                    discovery=card,
                    teacher_review=by_id.get(card.discovery_id)
                    or FindingDecision(candidate_id=card.discovery_id, status="pending"),
                )
                for card in discovery.findings
            )
            if discovery
            else (),
        )

    def create(self, principal: Principal, batch_id: str, body: AnalysisCreate) -> AnalysisResult:
        if body.discovery is not None:
            raise ValueError("Use the bounded async discovery save path")
        with self.database.transaction() as connection:
            batch = self._batch(connection, principal, batch_id, body.snapshot_id)
            require_owner(batch, principal)
        spec = InsightSpec(
            snapshot_id=body.snapshot_id,
            conditions=body.conditions,
            include_dimensions=body.include_dimensions,
        )
        reader = InsightReader(self.database, clock=self.clock)
        with reader._snapshot(principal, batch_id, spec) as (connection, data, students, cards):
            batch = self._batch(connection, principal, batch_id, body.snapshot_id)
            require_owner(batch, principal)
            available = {card.candidate_id: card for card in cards}
            ids = body.candidate_ids if body.candidate_ids is not None else tuple(available)
            if any(candidate_id not in available for candidate_id in ids):
                deny(404)
            if body.conditions:
                selected = select_students(
                    data,
                    batch_id=batch_id,
                    conditions=tuple(
                        Condition(
                            DIMENSION_MAP[c.dimension_code],
                            OPERATOR_MAP[c.operator],
                            Fraction(c.value),
                        )
                        for c in body.conditions
                    ),
                )
                member_ids = set(selected.student_ids)
                members = tuple(s for s in students if s.student_id in member_ids)
            else:
                members = students
            snapshot = AnalysisSnapshot(
                query=spec,
                statistics=self._statistics(batch, data),
                group_statistics=self._statistics(
                    batch,
                    data,
                    student_ids=tuple(s.student_id for s in members),
                    kind="matched_group",
                )
                if body.conditions
                else None,
                total_count=len(students),
                matched_count=len(members),
                ratio=stat(Fraction(len(members), len(students))),
                members=members,
                candidates=tuple(
                    card_view(available[candidate_id], spec, batch_id) for candidate_id in ids
                ),
            )
        return self._save(principal, batch_id, body, snapshot)

    def _save(
        self,
        principal: Principal,
        batch_id: str,
        body: AnalysisCreate,
        snapshot: AnalysisSnapshot,
        *,
        expected_input: DiscoveryInput | None = None,
    ) -> AnalysisResult:
        payload = snapshot.model_dump_json()
        if len(payload.encode()) > MAX_SNAPSHOT_BYTES:
            raise APIError(413, "ANALYSIS_RESULT_TOO_LARGE", "分析结果超过保存限制")
        # Computation finishes before the short write lock. Recheck current credentials/access.
        with self.database.transaction(write=True) as connection:
            batch = self._batch(connection, principal, batch_id, body.snapshot_id)
            require_owner(batch, principal)
            if expected_input is not None:
                current = DiscoveryInputReader(self.database, clock=self.clock)._load_input(
                    connection, principal, batch_id, expected_input.spec
                )
                if current != expected_input:
                    fail("DATA_INTEGRITY_ERROR", 503)
            title = checked_text(connection, batch["data_scope_id"], body.title)
            note = checked_text(connection, batch["data_scope_id"], body.note)
            result_id, now = str(uuid4()), stamp(self.clock())
            connection.execute(
                "INSERT INTO analysis_results VALUES (?,?,?,?,?,?,?)",
                (
                    result_id,
                    batch_id,
                    body.snapshot_id,
                    payload,
                    hashlib.sha256(payload.encode()).hexdigest(),
                    principal.teacher_id,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO analysis_result_versions VALUES (?,?,?,?,?,?,?)",
                (result_id, 1, title, note, "[]", principal.teacher_id, now),
            )
            return self._dto(*self._rows(connection, batch_id, result_id))

    # Reuse the deterministic statistics adapter without adding a new scoring rule.
    _statistics = staticmethod(InsightReader._statistics)

    def get(
        self, principal: Principal, batch_id: str, result_id: str, *, snapshot_id: str | None = None
    ) -> AnalysisResult:
        with self.database.transaction() as connection:
            self._batch(connection, principal, batch_id, snapshot_id)
            return self._dto(*self._rows(connection, batch_id, result_id))

    def list(
        self,
        principal: Principal,
        batch_id: str,
        *,
        snapshot_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> AnalysisList:
        validate_page(offset, limit)
        with self.database.transaction() as connection:
            batch = self._batch(connection, principal, batch_id, snapshot_id)
            total = connection.execute(
                "SELECT COUNT(*) FROM analysis_results WHERE batch_id=?", (batch_id,)
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT result_id FROM analysis_results WHERE batch_id=? "
                "ORDER BY created_at,result_id LIMIT ? OFFSET ?",
                (batch_id, limit, offset),
            ).fetchall()
            items = tuple(
                self._summary(*self._rows(connection, batch_id, r["result_id"])) for r in rows
            )
            return AnalysisList(
                batch_id=batch_id,
                snapshot_id=batch["snapshot_id"],
                total_count=total,
                items=items,
                page=_page(offset, limit, len(items), total),
            )

    def patch(
        self, principal: Principal, batch_id: str, result_id: str, body: AnalysisPatch
    ) -> AnalysisResult:
        with self.database.transaction(write=True) as connection:
            batch = self._batch(connection, principal, batch_id, body.snapshot_id)
            require_owner(batch, principal)
            row, version = self._rows(connection, batch_id, result_id)
            if body.expected_version != version["version"]:
                version_conflict("ANALYSIS_RESULT_VERSION_MISMATCH")
            result = self._dto(row, version)
            decisions = {
                finding.candidate.candidate_id: finding.teacher_review
                for finding in result.findings
            }
            decisions.update(
                {
                    finding.discovery.discovery_id: finding.teacher_review
                    for finding in result.discovery_findings
                }
            )
            for decision in body.decisions:
                if decision.candidate_id not in decisions:
                    deny(404)
                decisions[decision.candidate_id] = decision.model_copy(
                    update={"note": checked_text(connection, batch["data_scope_id"], decision.note)}
                )
            title = (
                checked_text(connection, batch["data_scope_id"], body.title)
                if body.title is not None
                else result.title
            )
            note = (
                checked_text(connection, batch["data_scope_id"], body.note)
                if body.note is not None
                else result.note
            )
            connection.execute(
                "INSERT INTO analysis_result_versions VALUES (?,?,?,?,?,?,?)",
                (
                    result_id,
                    result.version + 1,
                    title,
                    note,
                    json.dumps([d.model_dump() for d in decisions.values()], ensure_ascii=False),
                    principal.teacher_id,
                    stamp(self.clock()),
                ),
            )
            return self._dto(*self._rows(connection, batch_id, result_id))
