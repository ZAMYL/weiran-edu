"""Offline report generation; formal download authorization belongs to the backend."""

from .adapters import from_statistics
from .models import (
    AnonymousStudent,
    Condition,
    DetailAuthorization,
    DimensionReport,
    ExportLimits,
    Frequency,
    GroupGrant,
    GroupReport,
    ReportError,
    ReportFiles,
    ReportInput,
    Scale,
    Score,
    Source,
)
from .render import generate_report

__all__ = [
    "AnonymousStudent",
    "Condition",
    "DetailAuthorization",
    "DimensionReport",
    "ExportLimits",
    "Frequency",
    "GroupGrant",
    "GroupReport",
    "ReportError",
    "ReportFiles",
    "ReportInput",
    "Scale",
    "Score",
    "Source",
    "from_statistics",
    "generate_report",
]
