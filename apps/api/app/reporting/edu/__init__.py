"""Offline report generation; formal download authorization belongs to the backend."""

from .adapters import from_statistics
from .models import (
    AnonymousStudent,
    Condition,
    DetailAuthorization,
    DimensionReport,
    ExportLimits,
    FindingMetric,
    Frequency,
    GroupGrant,
    GroupReport,
    ReportError,
    ReportFiles,
    ReportInput,
    ReviewedFinding,
    ReviewedFindings,
    Scale,
    Score,
    Source,
)
from .render import generate_report
from .reviewed import with_reviewed_findings

__all__ = [
    "AnonymousStudent",
    "Condition",
    "DetailAuthorization",
    "DimensionReport",
    "ExportLimits",
    "FindingMetric",
    "Frequency",
    "GroupGrant",
    "GroupReport",
    "ReportError",
    "ReportFiles",
    "ReportInput",
    "ReviewedFinding",
    "ReviewedFindings",
    "Scale",
    "Score",
    "Source",
    "from_statistics",
    "generate_report",
    "with_reviewed_findings",
]
