"""One metadata rule set for fail-closed fact reads and owner diagnostics."""

import sqlite3
from collections import Counter

from app.importing.edu.types import DIMENSIONS

from .diagnostic_models import IntegrityCheck, IntegrityCode, IntegrityInspection
from .service import fail

SPECS = {spec.code: spec for spec in DIMENSIONS}
MESSAGES: dict[IntegrityCode, str] = {
    "STUDENT_COUNT": "批次学生数量一致",
    "MEMBER_SCOPE": "成员存在且属于同一数据范围",
    "RUBRIC_DIMENSIONS": "评分标准包含完整且唯一的五维映射",
    "RUBRIC_SOURCE": "评分标准来源摘要、工作表及范围一致",
    "EVALUATION_COUNT": "评分数量与批次及五维人数一致",
    "EVALUATION_MEMBERSHIP": "评分属于本批学生及评分标准",
    "STUDENT_DIMENSIONS": "每位学生具有完整且唯一的五维评分",
    "SCORE_RANGE": "评分处于合法区间",
    "EVIDENCE_SOURCE_HASH": "评分证据来源摘要一致",
    "EVIDENCE_COORDINATES": "证据工作表、行号及单元格坐标一致",
}


def inspect_snapshot(connection: sqlite3.Connection, batch: sqlite3.Row) -> IntegrityInspection:
    """Inspect the complete snapshot, never identities, text bodies or workbook BLOBs.

    Counts describe failed rules/rows, not a list of independent root causes. All
    output strings come from fixed code, never possibly damaged database values.
    """
    batch_id, file_hash = batch["batch_id"], batch["file_sha256"]
    members = connection.execute(
        "SELECT bs.student_pk,bs.data_scope_id,s.data_scope_id AS student_scope "
        "FROM batch_students bs LEFT JOIN students s ON s.student_pk=bs.student_pk "
        "WHERE bs.batch_id=?",
        (batch_id,),
    ).fetchall()
    criteria = connection.execute(
        "SELECT dimension_code,file_sha256,sheet_name,source_range "
        "FROM rubric_criteria WHERE rubric_id=?",
        (batch["rubric_id"],),
    ).fetchall()
    scores = connection.execute(
        "SELECT student_pk,rubric_id,dimension_code,score_units,file_sha256,sheet_name,"
        "source_row,dimension_cell,score_cell,comment_cell FROM evaluations WHERE batch_id=?",
        (batch_id,),
    ).fetchall()
    checks: list[IntegrityCheck] = []

    def add(code: IntegrityCode, violations: int) -> None:
        checks.append(
            IntegrityCheck(
                code=code,
                message=MESSAGES[code],
                passed=violations == 0,
                violation_count=violations,
            )
        )

    add("STUDENT_COUNT", abs(len(members) - batch["student_count"]))
    add(
        "MEMBER_SCOPE",
        sum(
            row["data_scope_id"] != batch["data_scope_id"]
            or row["student_scope"] != batch["data_scope_id"]
            for row in members
        ),
    )
    add(
        "RUBRIC_DIMENSIONS",
        int(len(criteria) != len(SPECS) or {r["dimension_code"] for r in criteria} != set(SPECS)),
    )
    add(
        "RUBRIC_SOURCE",
        sum(
            row["file_sha256"] != file_hash
            or row["sheet_name"] != "Sheet1"
            or row["dimension_code"] not in SPECS
            or row["source_range"] != SPECS[row["dimension_code"]].rubric_range
            for row in criteria
        ),
    )
    add(
        "EVALUATION_COUNT",
        int(len(scores) != batch["evaluation_count"] or len(scores) != len(members) * len(SPECS)),
    )
    dimensions: dict[str, list[str]] = {row["student_pk"]: [] for row in members}
    for row in scores:
        if row["student_pk"] in dimensions:
            dimensions[row["student_pk"]].append(row["dimension_code"])
    add(
        "EVALUATION_MEMBERSHIP",
        sum(
            row["student_pk"] not in dimensions or row["rubric_id"] != batch["rubric_id"]
            for row in scores
        ),
    )
    add(
        "STUDENT_DIMENSIONS",
        sum(len(codes) != len(SPECS) or set(codes) != set(SPECS) for codes in dimensions.values()),
    )
    add("SCORE_RANGE", sum(not 100 <= row["score_units"] <= 500 for row in scores))
    add("EVIDENCE_SOURCE_HASH", sum(row["file_sha256"] != file_hash for row in scores))
    source_rows = Counter(row["source_row"] for row in scores)
    add(
        "EVIDENCE_COORDINATES",
        sum(
            row["sheet_name"] != "Sheet2"
            or row["source_row"] <= 1
            or source_rows[row["source_row"]] != 1
            or row["dimension_cell"] != f"B{row['source_row']}"
            or row["score_cell"] != f"C{row['source_row']}"
            or row["comment_cell"] != f"D{row['source_row']}"
            for row in scores
        ),
    )
    return IntegrityInspection(
        passed=all(check.passed for check in checks),
        expected_student_count=batch["student_count"],
        actual_student_count=len(members),
        expected_evaluation_count=batch["evaluation_count"],
        actual_evaluation_count=len(scores),
        checks=tuple(checks),
    )


def validate_snapshot(connection: sqlite3.Connection, batch: sqlite3.Row) -> None:
    """Keep existing readers fail closed; detailed reports use the same rules."""
    if not inspect_snapshot(connection, batch).passed:
        fail("DATA_INTEGRITY_ERROR", 503)
