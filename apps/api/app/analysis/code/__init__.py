"""Reusable local code-analysis kernel (DS-03).

The kernel extracts deterministic structural features from submitted programs,
associates them with judge outcomes and test-point evidence, and emits *pattern
candidates* -- all without executing untrusted student code and without any
pre-baked conclusion.

It is self-contained: the input contract (:mod:`app.analysis.code.contract`) is
deliberately small so it can later be adapted to the B-01 acceptance data model
(PR #105), whose fields are not yet validated. It does not reimplement the
``problem_patterns`` base counting.

Three explanation levels are kept structurally distinct (:mod:`app.analysis.code.explanation`):
observed facts, statistical associations, and unverified hypotheses.
"""

from .association import AssociationState, Contingency, FeatureAssociation
from .contract import (
    CodeAnalysisError,
    Language,
    SourceRef,
    SubmissionRecord,
    SubmissionRef,
    TestPointOutcome,
    TestPointResult,
    Verdict,
)
from .engine import analyze
from .explanation import Hypothesis, HypothesisStatus, ObservedFact, StatisticalAssociation
from .features import (
    Confidence,
    FeatureId,
    FeatureSet,
    FeatureValue,
    ParseStatus,
    is_present,
    presence_threshold,
)
from .interop import (
    C01FeatureAssociation,
    b01_language,
    b01_test_point_outcome,
    b01_verdict,
    c01_feature_associations,
    c01_feature_code,
    c01_status,
)
from .languages import CppExtractor, PythonExtractor, extractor_for
from .results import (
    Analysis,
    OutcomeCounts,
    PatternCandidate,
    SubmissionFeatures,
    TestPointAggregate,
)

__all__ = [
    "Analysis",
    "AssociationState",
    "C01FeatureAssociation",
    "CodeAnalysisError",
    "Confidence",
    "Contingency",
    "CppExtractor",
    "FeatureAssociation",
    "FeatureId",
    "FeatureSet",
    "FeatureValue",
    "Hypothesis",
    "HypothesisStatus",
    "Language",
    "ObservedFact",
    "OutcomeCounts",
    "ParseStatus",
    "PatternCandidate",
    "PythonExtractor",
    "SourceRef",
    "StatisticalAssociation",
    "SubmissionFeatures",
    "SubmissionRecord",
    "SubmissionRef",
    "TestPointAggregate",
    "TestPointOutcome",
    "TestPointResult",
    "Verdict",
    "analyze",
    "b01_language",
    "b01_test_point_outcome",
    "b01_verdict",
    "c01_feature_associations",
    "c01_feature_code",
    "c01_status",
    "extractor_for",
    "is_present",
    "presence_threshold",
]
