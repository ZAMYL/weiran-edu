"""Pure projections for a pending assessment import.

This module never authenticates a user, opens a transaction, or accesses raw identity
columns. The service owns those responsibilities and the private source BLOB.
"""

import hashlib
import json

from app.importing.edu import ParsedWorkbook, build_preview
from app.importing.edu.quality import QualityReport


def project_import_payload(parsed: ParsedWorkbook, report: QualityReport) -> dict[str, object]:
    """Keep the existing paginated public preview representation and row ordering."""
    return {
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


def rubric_fingerprint(parsed: ParsedWorkbook) -> str | None:
    """Only a rubric without Sheet1 errors is eligible for the original v1 hash."""
    if any(issue.sheet_name == "Sheet1" and issue.severity == "error" for issue in parsed.issues):
        return None
    serialized = json.dumps(
        [[rubric.dimension_code, rubric.title, rubric.text_cells] for rubric in parsed.rubrics],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "rubric.v1." + hashlib.sha256(serialized.encode()).hexdigest()
