"""Safe diagnostic projections: fixed codes, counts and the caller's permissions."""

from typing import Literal

from .models import Identifier, Model

type IntegrityCode = Literal[
    "STUDENT_COUNT",
    "MEMBER_SCOPE",
    "RUBRIC_DIMENSIONS",
    "RUBRIC_SOURCE",
    "EVALUATION_COUNT",
    "EVALUATION_MEMBERSHIP",
    "STUDENT_DIMENSIONS",
    "SCORE_RANGE",
    "EVIDENCE_SOURCE_HASH",
    "EVIDENCE_COORDINATES",
]
type Action = Literal[
    "read_batch",
    "import_data",
    "diagnose_integrity",
    "review_text",
    "save_analysis",
    "edit_analysis",
    "export_analysis",
]


class IntegrityCheck(Model):
    code: IntegrityCode
    message: str
    passed: bool
    violation_count: int


class IntegrityInspection(Model):
    passed: bool
    expected_student_count: int
    actual_student_count: int
    expected_evaluation_count: int
    actual_evaluation_count: int
    checks: tuple[IntegrityCheck, ...]


class IntegrityRequest(Model):
    snapshot_id: Identifier


class IntegrityReport(IntegrityInspection):
    batch_id: str
    snapshot_id: str
    checked_at: str
    scope: Literal["stored_fact_metadata"] = "stored_fact_metadata"
    repaired: Literal[False] = False


class Capability(Model):
    action: Action
    available: bool
    allowed: bool
    reason: Literal[
        "allowed", "owner_required", "feature_unavailable", "import_permission_required"
    ]


class BatchCapabilities(Model):
    batch_id: str
    snapshot_id: str
    role: Literal["owner", "reader"]
    operations: tuple[Capability, ...]
