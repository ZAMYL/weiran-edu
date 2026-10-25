"""Server-only bridge from a checked DB snapshot to the existing summary report core."""

import sqlite3
from dataclasses import replace
from datetime import datetime
from fractions import Fraction

from app.analysis.edu import select_students
from app.analysis.edu.models import Condition
from app.core.errors import APIError
from app.reporting.edu import (
    AnonymousStudent,
    GroupReport,
    ReportInput,
    Score,
    from_statistics,
    with_reviewed_findings,
)
from app.reporting.edu.adapters import _dimensions, conditions_from_dto

from .analysis import DIMENSION_MAP, OPERATOR_MAP, AnalysisReader
from .models import Rubric, RubricCriterion, RubricSource, Student
from .review_models import AnalysisResult
from .service import fail


def _members(students: tuple[Student, ...]) -> dict:
    return {
        student.student_id: tuple(
            sorted((cell.dimension_code, cell.score, cell.evidence_id) for cell in student.scores)
        )
        for student in students
    }


def report_input(
    connection: sqlite3.Connection,
    batch: sqlite3.Row,
    result: AnalysisResult,
    criteria: list[dict],
    *,
    generated_at: datetime,
) -> ReportInput:
    """Called inside the export's authenticated read transaction, never with client facts.

    Reuse full-snapshot loading and deterministic statistics/selection. No HTTP pages are
    fabricated: the internal GroupReport carries all actual matches from this snapshot.
    """
    if len(result.analysis.members) > 5000:
        raise APIError(413, "EXPORT_TOO_LARGE", "报告超过当前同步导出限制")
    data, students = AnalysisReader._load(connection, batch)
    statistics = AnalysisReader._statistics(batch, data)
    if statistics != result.analysis.statistics:
        fail("DATA_INTEGRITY_ERROR", 503)
    rubric = Rubric(
        batch_id=batch["batch_id"],
        snapshot_id=batch["snapshot_id"],
        rubric_id=batch["rubric_id"],
        rubric_version=batch["rubric_version"],
        criteria=tuple(
            RubricCriterion(
                rubric_criterion_id=c["rubric_criterion_id"],
                dimension_code=c["dimension_code"],
                display_name=c["dimension_code"],
                original_name=c["dimension_code"],
                source=RubricSource(
                    file_sha256=c["file_sha256"],
                    sheet_name=c["sheet_name"],
                    range=c["source_range"],
                ),
            )
            for c in criteria
        ),
    )
    # Only scale/version and code mapping cross the core whitelist, never reviewed text.
    report = from_statistics(statistics, generated_at=generated_at, rubric=rubric)
    query = result.analysis.query
    if query.conditions:
        selected = select_students(
            data,
            batch_id=batch["batch_id"],
            conditions=tuple(
                Condition(
                    DIMENSION_MAP[c.dimension_code], OPERATOR_MAP[c.operator], Fraction(c.value)
                )
                for c in query.conditions
            ),
        )
        ids = set(selected.student_ids)
        matches = tuple(s for s in students if s.student_id in ids)
        group_stats = AnalysisReader._statistics(
            batch, data, student_ids=selected.student_ids, kind="matched_group"
        )
        if group_stats != result.analysis.group_statistics:
            fail("DATA_INTEGRITY_ERROR", 503)
        internal = GroupReport(
            report.source,
            "saved-group-" + result.result_id,
            conditions_from_dto(query.conditions),
            len(matches),
            len(students),
            selected.eligible_count,
            len(selected.unevaluable_records),
            selected.matched_share.proportion,
            selected.matched_among_eligible.proportion,
            statistics.rule_version,
            tuple(
                AnonymousStudent(
                    report.source,
                    s.student_id,
                    tuple(
                        Score(c.dimension_code, Fraction(str(c.score)), c.evidence_id)
                        for c in s.scores
                    ),
                )
                for s in matches
            ),
            _dimensions(
                group_stats,
                report.source,
                len(matches),
                {d.code: d.scale for d in report.dimensions},
                kind="matched_group",
            ),
        )
        report = replace(report, groups=(internal,))
    else:
        matches = students
        if result.analysis.group_statistics is not None:
            fail("DATA_INTEGRITY_ERROR", 503)
    if _members(matches) != _members(result.analysis.members):
        fail("DATA_INTEGRITY_ERROR", 503)
    return with_reviewed_findings(
        report, result, expected_result_id=result.result_id, expected_version=result.version
    )
