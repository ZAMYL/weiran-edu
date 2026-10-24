"""Authorized SQLite snapshots adapted to the existing exact analysis engine."""

import sqlite3
from collections import Counter
from fractions import Fraction
from typing import Literal

from app.analysis.edu.engine import select_students, summarize
from app.analysis.edu.models import (
    AnalysisInputError,
    AssessmentRecord,
    Condition,
    Dataset,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    Operator,
    ScorePolicy,
)
from app.analysis.edu.validation import prepare_dataset
from app.core.errors import APIError

from .analysis_models import (
    AnalysisScope,
    DimensionCode,
    DimensionStatistics,
    ExactRational,
    Frequency,
    GroupQuery,
    GroupResult,
    MatrixCell,
    ScoreMatrix,
    Statistics,
    StatValue,
)
from .auth import Principal
from .models import Student, StudentScore
from .reads import BatchReader
from .service import _page, fail

RULE_VERSION = "edu.statistics.v1"
DIMENSION_MAP: dict[DimensionCode, Dimension] = {
    "algorithm_data_structure": Dimension.ALGORITHMS,
    "code_implementation": Dimension.IMPLEMENTATION,
    "problem_modeling": Dimension.MODELING,
    "efficiency_awareness": Dimension.EFFICIENCY,
    "solution_strategy": Dimension.STRATEGY,
}
CODE_MAP = {dimension: code for code, dimension in DIMENSION_MAP.items()}
OPERATOR_MAP = {
    "lt": Operator.LT,
    "lte": Operator.LE,
    "eq": Operator.EQ,
    "gte": Operator.GE,
    "gt": Operator.GT,
}


def stat(value: Fraction | None) -> StatValue:
    if value is None:
        return StatValue(value=None, exact=None, display="暂无有效数据")
    # Integer arithmetic implements exact HALF_UP even at rational ties.
    magnitude = abs(value) * 100
    units = (2 * magnitude.numerator + magnitude.denominator) // (2 * magnitude.denominator)
    sign = "-" if value < 0 and units else ""
    return StatValue(
        value=float(value),
        exact=ExactRational(numerator=value.numerator, denominator=value.denominator),
        display=f"{sign}{units // 100}.{units % 100:02d}",
    )


class AnalysisReader(BatchReader):
    @staticmethod
    def _load(
        connection: sqlite3.Connection, batch: sqlite3.Row
    ) -> tuple[Dataset, tuple[Student, ...]]:
        batch_id = batch["batch_id"]
        members = connection.execute(
            "SELECT s.student_pk,s.public_id,bs.display_label FROM batch_students bs "
            "JOIN students s ON s.student_pk=bs.student_pk WHERE bs.batch_id=? "
            "ORDER BY bs.display_order,s.public_id",
            (batch_id,),
        ).fetchall()
        scores = connection.execute(
            "SELECT e.student_pk,e.dimension_code,e.score_units,e.evidence_id "
            "FROM evaluations e JOIN rubric_criteria r ON r.rubric_id=e.rubric_id "
            "AND r.dimension_code=e.dimension_code WHERE e.batch_id=? "
            "AND e.rubric_id=? AND e.file_sha256=? AND r.file_sha256=?",
            (batch_id, batch["rubric_id"], batch["file_sha256"], batch["file_sha256"]),
        ).fetchall()
        actual_scores = connection.execute(
            "SELECT COUNT(*) FROM evaluations WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        if (
            len(members) != batch["student_count"]
            or len(scores) != len(members) * 5
            or len(scores) != actual_scores
            or len(scores) != batch["evaluation_count"]
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        grouped: dict[str, dict[str, sqlite3.Row]] = {m["student_pk"]: {} for m in members}
        for row in scores:
            values = grouped.get(row["student_pk"])
            if (
                values is None
                or row["dimension_code"] not in DIMENSION_MAP
                or row["dimension_code"] in values
                or not 100 <= row["score_units"] <= 500
            ):
                fail("DATA_INTEGRITY_ERROR", 503)
            values[row["dimension_code"]] = row
        records, students = [], []
        for member in members:
            values = grouped[member["student_pk"]]
            if set(values) != set(DIMENSION_MAP):
                fail("DATA_INTEGRITY_ERROR", 503)
            records.append(
                AssessmentRecord(
                    batch_id=batch_id,
                    student_id=member["public_id"],
                    record_id=member["public_id"],
                    source_id=batch["file_sha256"],
                    scores={
                        dimension: Fraction(values[code]["score_units"], 100)
                        for code, dimension in DIMENSION_MAP.items()
                    },
                    evidence_refs=tuple(
                        EvidenceRef(
                            batch["file_sha256"],
                            values[code]["evidence_id"],
                            EvidenceKind.SCORE,
                            dim,
                        )
                        for code, dim in DIMENSION_MAP.items()
                    ),
                )
            )
            students.append(
                Student(
                    student_id=member["public_id"],
                    display_label=member["display_label"],
                    scores=tuple(
                        StudentScore(
                            dimension_code=code,
                            score=values[code]["score_units"] / 100,
                            evidence_id=values[code]["evidence_id"],
                        )
                        for code in DIMENSION_MAP
                    ),
                )
            )
        try:
            dataset = prepare_dataset(records, policy=ScorePolicy(step=Fraction(1, 100)))
        except AnalysisInputError:
            fail("DATA_INTEGRITY_ERROR", 503)
        if dataset.issues:
            fail("DATA_INTEGRITY_ERROR", 503)
        return dataset, tuple(students)

    @staticmethod
    def _statistics(
        batch: sqlite3.Row,
        dataset: Dataset,
        *,
        student_ids: tuple[str, ...] | None = None,
        kind: Literal["batch", "matched_group"] = "batch",
        matrix: ScoreMatrix | None = None,
    ) -> Statistics:
        summary = summarize(dataset, batch_id=batch["batch_id"], student_ids=student_ids)
        return Statistics(
            batch_id=batch["batch_id"],
            snapshot_id=batch["snapshot_id"],
            rule_version=RULE_VERSION,
            scope=AnalysisScope(kind=kind, record_count=summary.scope.student_count),
            dimensions=tuple(
                DimensionStatistics(
                    dimension_code=CODE_MAP[d.dimension],
                    valid_count=d.valid_count,
                    missing_count=d.missing_count,
                    invalid_count=d.invalid_count,
                    mean=stat(d.mean),
                    median=stat(d.median),
                    minimum=stat(d.minimum),
                    maximum=stat(d.maximum),
                    distribution=tuple(
                        Frequency(
                            score=float(f.score),
                            count=f.share.count,
                            denominator=f.share.denominator,
                            ratio=stat(f.share.proportion),
                        )
                        for f in d.distribution
                    ),
                )
                for d in summary.dimensions
            ),
            matrix=matrix,
        )

    def statistics(
        self,
        principal: Principal,
        batch_id: str,
        *,
        snapshot_id: str | None = None,
        x_dimension: DimensionCode | None = None,
        y_dimension: DimensionCode | None = None,
    ) -> Statistics:
        with self.database.transaction() as connection:
            batch = self._batch(connection, principal, batch_id, snapshot_id)
            if (x_dimension is None) != (y_dimension is None) or (
                x_dimension is not None
                and (
                    x_dimension == y_dimension
                    or x_dimension not in DIMENSION_MAP
                    or y_dimension not in DIMENSION_MAP
                )
            ):
                raise APIError(422, "VALIDATION_ERROR", "二维矩阵需要两个不同的有效维度")
            dataset, _ = self._load(connection, batch)
        matrix = None
        if x_dimension is not None and y_dimension is not None:
            pairs = Counter(
                (
                    record.score(DIMENSION_MAP[x_dimension]).value,
                    record.score(DIMENSION_MAP[y_dimension]).value,
                )
                for record in dataset.records
            )
            matrix = ScoreMatrix(
                x_dimension=x_dimension,
                y_dimension=y_dimension,
                x_values=tuple(float(x) for x in sorted({x for x, _ in pairs})),
                y_values=tuple(float(y) for y in sorted({y for _, y in pairs})),
                paired_count=len(dataset.records),
                excluded_count=0,
                cells=tuple(
                    MatrixCell(x=float(x), y=float(y), count=count)
                    for (x, y), count in sorted(pairs.items())
                ),
            )
        return self._statistics(batch, dataset, matrix=matrix)

    def group(self, principal: Principal, batch_id: str, query: GroupQuery) -> GroupResult:
        with self.database.transaction() as connection:
            batch = self._batch(connection, principal, batch_id, query.snapshot_id)
            dataset, students = self._load(connection, batch)
        selected = select_students(
            dataset,
            batch_id=batch_id,
            conditions=tuple(
                Condition(
                    DIMENSION_MAP[c.dimension_code], OPERATOR_MAP[c.operator], Fraction(c.value)
                )
                for c in query.conditions
            ),
        )
        matched_ids = set(selected.student_ids)
        matches = tuple(s for s in students if s.student_id in matched_ids)
        items = matches[query.offset : query.offset + query.limit]
        return GroupResult(
            batch_id=batch_id,
            snapshot_id=batch["snapshot_id"],
            conditions=query.conditions,
            rule_version=RULE_VERSION,
            matched_count=len(matches),
            total_count=selected.scope.student_count,
            ratio=float(selected.matched_share.proportion),
            eligible_count=selected.eligible_count,
            unevaluable_count=len(selected.unevaluable_records),
            matched_among_eligible_ratio=float(selected.matched_among_eligible.proportion),
            items=items,
            page=_page(query.offset, query.limit, len(items), len(matches)),
            statistics=self._statistics(
                batch, dataset, student_ids=selected.student_ids, kind="matched_group"
            )
            if query.include_statistics
            else None,
        )
