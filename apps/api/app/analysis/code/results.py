"""Structured output of the code-analysis kernel.

Every aggregate keeps its denominators, missing/unparseable counts and source
references, so a later Agent can trace a candidate back to concrete submissions
and test points. Results are computed dynamically from the supplied records; no
pattern or conclusion is pre-baked.
"""

from dataclasses import dataclass

from .association import FeatureAssociation
from .contract import SourceRef, SubmissionRef, TestPointOutcome, TestPointResult, Verdict
from .explanation import Hypothesis, ObservedFact
from .features import FeatureId, FeatureSet, presence_threshold


@dataclass(frozen=True)
class SubmissionFeatures:
    reference: SubmissionRef
    verdict: Verdict
    features: FeatureSet
    test_points: tuple[TestPointResult, ...]


@dataclass(frozen=True)
class OutcomeCounts:
    passed: int
    failed: int
    other: int

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.other

    @property
    def judged(self) -> int:
        return self.passed + self.failed


@dataclass(frozen=True)
class TestPointAggregate:
    testpoint_id: str
    passed: int
    failed: int
    unknown: int

    @property
    def attempted(self) -> int:
        return self.passed + self.failed


@dataclass(frozen=True)
class PatternCandidate:
    """A measurable feature/outcome association offered for further investigation."""

    feature: FeatureId
    association: FeatureAssociation
    observed: tuple[ObservedFact, ...]
    hypothesis: tuple[Hypothesis, ...]
    limits: tuple[str, ...]
    evidence: tuple[SourceRef, ...]


@dataclass(frozen=True)
class Analysis:
    batch_id: str
    problem_id: str
    submissions: tuple[SubmissionFeatures, ...]
    outcomes: OutcomeCounts
    associations: tuple[FeatureAssociation, ...]
    test_points: tuple[TestPointAggregate, ...]
    candidates: tuple[PatternCandidate, ...]
    limits: tuple[str, ...]

    def association_for(self, feature: FeatureId) -> FeatureAssociation | None:
        return next((a for a in self.associations if a.feature is feature), None)

    def candidate_for(self, feature: FeatureId) -> PatternCandidate | None:
        return next((c for c in self.candidates if c.feature is feature), None)

    def matching_submissions(
        self, feature: FeatureId, *, present: bool = True, threshold: int | None = None
    ) -> tuple[SubmissionRef, ...]:
        """Submissions whose ``feature`` value satisfies the given condition.

        ``present=True`` keeps submissions where the feature value is not
        ``UNKNOWN`` and meets the threshold; ``present=False`` keeps submissions
        with a known value that does not meet it. Unknown values are never
        counted as absent.
        """

        bound = threshold if threshold is not None else presence_threshold(feature)
        matches = []
        for submission in self.submissions:
            value = submission.features.get(feature).value
            if value is None:
                continue
            if isinstance(value, bool):
                kept = value is present
            else:
                kept = (value >= bound) is present
            if kept:
                matches.append(submission.reference)
        return tuple(matches)

    def submissions_failing_testpoint(self, testpoint_id: str) -> tuple[SubmissionRef, ...]:
        return tuple(
            submission.reference
            for submission in self.submissions
            if any(
                point.testpoint_id == testpoint_id and point.outcome is TestPointOutcome.FAILED
                for point in submission.test_points
            )
        )

    def failed_verdict_submissions(self) -> tuple[SubmissionRef, ...]:
        return tuple(
            submission.reference for submission in self.submissions if submission.verdict.is_failure
        )
