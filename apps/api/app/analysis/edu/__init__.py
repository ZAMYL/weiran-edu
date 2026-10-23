"""Deterministic in-memory analytics. These Python objects are not a public API contract."""

from .correlation import spearman_correlations
from .engine import select_students, student_profile, summarize
from .models import (
    AnalysisInputError,
    AssessmentRecord,
    Condition,
    CorrelationState,
    Dataset,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    Operator,
    ScorePolicy,
    ScoreState,
)
from .validation import prepare_dataset

__all__ = [
    "AnalysisInputError",
    "AssessmentRecord",
    "Condition",
    "CorrelationState",
    "Dataset",
    "Dimension",
    "EvidenceKind",
    "EvidenceRef",
    "Operator",
    "ScorePolicy",
    "ScoreState",
    "prepare_dataset",
    "select_students",
    "spearman_correlations",
    "student_profile",
    "summarize",
]
