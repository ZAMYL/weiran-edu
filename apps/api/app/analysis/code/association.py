"""Statistical association between a code feature and a judge outcome.

Association is a 2x2 co-occurrence (feature present/absent x outcome
failed/passed), computed with exact rational proportions. It is a *statistical
association*, never a causal claim; consumers must keep it on the second level of
the explanation boundary.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from .features import FeatureId


class AssociationState(Enum):
    COMPUTABLE = "computable"
    INSUFFICIENT_SAMPLES = "insufficient_samples"
    NO_VARIATION = "no_variation"


@dataclass(frozen=True)
class Contingency:
    feature_failed: int
    feature_passed: int
    no_feature_failed: int
    no_feature_passed: int

    @property
    def feature_total(self) -> int:
        return self.feature_failed + self.feature_passed

    @property
    def no_feature_total(self) -> int:
        return self.no_feature_failed + self.no_feature_passed

    @property
    def failed_total(self) -> int:
        return self.feature_failed + self.no_feature_failed

    @property
    def passed_total(self) -> int:
        return self.feature_passed + self.no_feature_passed

    @property
    def feature_failure_rate(self) -> Fraction | None:
        return Fraction(self.feature_failed, self.feature_total) if self.feature_total else None

    @property
    def no_feature_failure_rate(self) -> Fraction | None:
        if not self.no_feature_total:
            return None
        return Fraction(self.no_feature_failed, self.no_feature_total)

    @property
    def failure_rate_difference(self) -> Fraction | None:
        left = self.feature_failure_rate
        right = self.no_feature_failure_rate
        return left - right if left is not None and right is not None else None


@dataclass(frozen=True)
class FeatureAssociation:
    feature: FeatureId
    state: AssociationState
    contingency: Contingency
    excluded_count: int


def build_association(
    feature: FeatureId,
    present: Sequence[bool | None],
    failed: Sequence[bool | None],
    *,
    minimum_samples: int = 2,
) -> FeatureAssociation:
    """Build a feature/outcome association from two aligned, per-submission sequences.

    Rows where either value is ``None`` (unknown feature value or unjudged
    verdict) are excluded and counted, never imputed.
    """

    if (
        isinstance(minimum_samples, bool)
        or not isinstance(minimum_samples, int)
        or minimum_samples < 2
    ):
        raise ValueError("minimum_samples_must_be_an_integer_at_least_two")
    if len(present) != len(failed):
        raise ValueError("aligned_sequences_required")

    contingency = Contingency(0, 0, 0, 0)
    excluded = 0
    for has_feature, is_failure in zip(present, failed, strict=True):
        if has_feature is None or is_failure is None:
            excluded += 1
            continue
        if has_feature and is_failure:
            contingency = Contingency(
                contingency.feature_failed + 1,
                contingency.feature_passed,
                contingency.no_feature_failed,
                contingency.no_feature_passed,
            )
        elif has_feature:
            contingency = Contingency(
                contingency.feature_failed,
                contingency.feature_passed + 1,
                contingency.no_feature_failed,
                contingency.no_feature_passed,
            )
        elif is_failure:
            contingency = Contingency(
                contingency.feature_failed,
                contingency.feature_passed,
                contingency.no_feature_failed + 1,
                contingency.no_feature_passed,
            )
        else:
            contingency = Contingency(
                contingency.feature_failed,
                contingency.feature_passed,
                contingency.no_feature_failed,
                contingency.no_feature_passed + 1,
            )
    return FeatureAssociation(feature, _state(contingency, minimum_samples), contingency, excluded)


def _state(contingency: Contingency, minimum_samples: int) -> AssociationState:
    if contingency.feature_total == 0 or contingency.no_feature_total == 0:
        return AssociationState.NO_VARIATION
    if contingency.failed_total == 0 or contingency.passed_total == 0:
        return AssociationState.NO_VARIATION
    if (
        contingency.feature_total < minimum_samples
        or contingency.no_feature_total < minimum_samples
    ):
        return AssociationState.INSUFFICIENT_SAMPLES
    return AssociationState.COMPUTABLE
