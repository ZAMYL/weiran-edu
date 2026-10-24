"""Offline, evidence-grounded internal engine; no HTTP or external-model adapter."""

from .models import (
    EvidenceDocument,
    EvidenceGrant,
    EvidenceProvider,
    Limits,
    LLMClient,
    Outcome,
    RubricDocument,
    RubricGrant,
    RubricProvider,
    SyntheticAccess,
    SyntheticInput,
    ToolCall,
)
from .orchestrator import run_insights
from .tools import ToolExecutor

__all__ = [
    "EvidenceDocument",
    "EvidenceGrant",
    "EvidenceProvider",
    "LLMClient",
    "Limits",
    "Outcome",
    "RubricDocument",
    "RubricGrant",
    "RubricProvider",
    "SyntheticAccess",
    "SyntheticInput",
    "ToolCall",
    "ToolExecutor",
    "run_insights",
]
