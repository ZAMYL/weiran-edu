"""Validate complete fact metadata without reading private source contents."""

import sqlite3

from app.importing.edu.types import DIMENSIONS

from .service import fail

SPECS = {spec.code: spec for spec in DIMENSIONS}


def validate_snapshot(connection: sqlite3.Connection, batch: sqlite3.Row) -> None:
    """Run after authorization, in the same transaction as the eventual fact read.

    Validate the whole batch even for an empty page or one evidence item. This is
    a consistency check of stored metadata, not a reparse of the private workbook.
    """
    batch_id, file_hash = batch["batch_id"], batch["file_sha256"]
    members = connection.execute(
        "SELECT bs.student_pk,bs.data_scope_id,s.data_scope_id AS student_scope "
        "FROM batch_students bs LEFT JOIN students s ON s.student_pk=bs.student_pk "
        "WHERE bs.batch_id=?",
        (batch_id,),
    ).fetchall()
    if len(members) != batch["student_count"] or any(
        row["data_scope_id"] != batch["data_scope_id"]
        or row["student_scope"] != batch["data_scope_id"]
        for row in members
    ):
        fail("DATA_INTEGRITY_ERROR", 503)
    criteria = connection.execute(
        "SELECT dimension_code,file_sha256,sheet_name,source_range "
        "FROM rubric_criteria WHERE rubric_id=?",
        (batch["rubric_id"],),
    ).fetchall()
    if (
        len(criteria) != len(SPECS)
        or {row["dimension_code"] for row in criteria} != set(SPECS)
        or any(
            row["file_sha256"] != file_hash
            or row["sheet_name"] != "Sheet1"
            or row["source_range"] != SPECS[row["dimension_code"]].rubric_range
            for row in criteria
        )
    ):
        fail("DATA_INTEGRITY_ERROR", 503)
    scores = connection.execute(
        "SELECT student_pk,rubric_id,dimension_code,score_units,file_sha256,sheet_name,"
        "source_row,dimension_cell,score_cell,comment_cell FROM evaluations WHERE batch_id=?",
        (batch_id,),
    ).fetchall()
    if len(scores) != batch["evaluation_count"] or len(scores) != len(members) * len(SPECS):
        fail("DATA_INTEGRITY_ERROR", 503)
    dimensions: dict[str, set[str]] = {row["student_pk"]: set() for row in members}
    source_rows: set[int] = set()
    for row in scores:
        seen = dimensions.get(row["student_pk"])
        code, source_row = row["dimension_code"], row["source_row"]
        if (
            seen is None
            or code not in SPECS
            or code in seen
            or row["rubric_id"] != batch["rubric_id"]
            or not 100 <= row["score_units"] <= 500
            or row["file_sha256"] != file_hash
            or row["sheet_name"] != "Sheet2"
            or source_row <= 1
            or source_row in source_rows
            or row["dimension_cell"] != f"B{source_row}"
            or row["score_cell"] != f"C{source_row}"
            or row["comment_cell"] != f"D{source_row}"
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        seen.add(code)
        source_rows.add(source_row)
    if any(codes != set(SPECS) for codes in dimensions.values()):
        fail("DATA_INTEGRITY_ERROR", 503)
