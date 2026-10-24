"""Current teacher-template Excel ingestion."""

from .inspection import ExcelInspection, inspect_excel
from .parser import parse_excel
from .preview import build_preview
from .quality import build_quality_report
from .types import ImportFileError, ParsedWorkbook, ParseLimits

__all__ = [
    "ExcelInspection",
    "ImportFileError",
    "ParseLimits",
    "ParsedWorkbook",
    "build_preview",
    "build_quality_report",
    "inspect_excel",
    "parse_excel",
]
