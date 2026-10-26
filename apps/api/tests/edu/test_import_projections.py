"""Behavioral contract for the extraction of import-time public projections."""

import hashlib
import json

from app.edu.import_projections import project_import_payload, rubric_fingerprint
from app.importing.edu import build_preview, build_quality_report, parse_excel
from app.importing.edu.types import DIMENSIONS


def test_import_projection_matches_existing_public_pages_without_private_values(book, pack):
    parsed = parse_excel(pack(book))
    report = build_quality_report(parsed)
    actual = project_import_payload(parsed, report)
    expected = {
        "rows": [
            row.model_dump(mode="json")
            for offset in range(0, report.summary.parsed_evaluation_count, 100)
            for row in build_preview(parsed, row_offset=offset, row_limit=100).rows
        ],
        "students": [
            student.model_dump(mode="json")
            for offset in range(0, report.summary.complete_student_count, 100)
            for student in build_preview(
                parsed, student_offset=offset, student_limit=100
            ).student_samples
        ],
        "issues": [issue.model_dump(mode="json") for issue in report.issues],
    }
    assert actual == expected
    assert len(actual["rows"]) == 10
    assert len(actual["students"]) == 2
    encoded = json.dumps(actual, ensure_ascii=False)
    assert "合成姓名" not in encoded
    assert "合成评语 0:0" not in encoded


def test_import_projection_preserves_quality_issue_order_and_source_state(book, pack):
    book["Sheet2"]["C3"] = None
    parsed = parse_excel(pack(book))
    report = build_quality_report(parsed)
    actual = project_import_payload(parsed, report)
    assert actual["issues"] == [issue.model_dump(mode="json") for issue in report.issues]
    assert actual["rows"] == [
        row.model_dump(mode="json")
        for offset in range(0, report.summary.parsed_evaluation_count, 100)
        for row in build_preview(parsed, row_offset=offset, row_limit=100).rows
    ]


def test_rubric_fingerprint_preserves_v1_format_and_digest(book, pack):
    parsed = parse_excel(pack(book))
    text = json.dumps(
        [[item.dimension_code, item.title, item.text_cells] for item in parsed.rubrics],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected = "rubric.v1." + hashlib.sha256(text.encode()).hexdigest()
    assert rubric_fingerprint(parsed) == expected
    assert rubric_fingerprint(parsed) == expected


def test_rubric_fingerprint_rejects_sheet1_errors(book, pack):
    book["Sheet1"][DIMENSIONS[0].title_cell] = "纯合成错误维度"
    parsed = parse_excel(pack(book))
    assert any(
        issue.sheet_name == "Sheet1" and issue.severity == "error" for issue in parsed.issues
    )
    assert rubric_fingerprint(parsed) is None
