"""Internal candidates and exact metrics, never a public DTO or authorization token."""

import re
from dataclasses import dataclass, field
from fractions import Fraction

from ..models import AnalysisInputError, Condition, Dimension, EvidenceRef, RecordRef, Scope, number

ADAPTER_VERSION = "edu-insight-candidates-0.1.0-draft"


@dataclass(frozen=True)
class GroupRule:
    rule_id: str
    conditions: tuple[Condition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_.-]{1,64}", self.rule_id
        ):
            raise AnalysisInputError("safe_group_rule_id_required")
        if (
            not isinstance(self.conditions, tuple)
            or not self.conditions
            or any(not isinstance(item, Condition) for item in self.conditions)
        ):
            raise AnalysisInputError("typed_nonempty_group_conditions_required")


@dataclass(frozen=True)
class DistributionFocus:
    condition: Condition
    minimum_share: Fraction

    def __post_init__(self) -> None:
        if not isinstance(self.condition, Condition):
            raise AnalysisInputError("typed_focus_condition_required")
        share = number(self.minimum_share)
        if not 0 < share <= 1:
            raise AnalysisInputError("focus_share_outside_range")
        object.__setattr__(self, "minimum_share", share)


@dataclass(frozen=True)
class DiscoveryPolicy:
    minimum_public_count: int = 5
    sensitive_student_ids: frozenset[str] = field(default_factory=frozenset, repr=False)
    include_dimensions: bool = True

    def __post_init__(self) -> None:
        if type(self.minimum_public_count) is not int or self.minimum_public_count < 2:
            raise AnalysisInputError("public_minimum_at_least_two_required")
        if not isinstance(self.sensitive_student_ids, frozenset) or any(
            not isinstance(value, str) or not value for value in self.sensitive_student_ids
        ):
            raise AnalysisInputError("typed_sensitive_scope_required")
        if type(self.include_dimensions) is not bool:
            raise AnalysisInputError("boolean_dimension_option_required")


@dataclass(frozen=True)
class Metric:
    key: str
    value: int | Fraction | None
    unit: str
    denominator: int | None
    source_refs: tuple[RecordRef, ...] = field(repr=False)
    at_score: Fraction | None = None


@dataclass(frozen=True)
class EvidenceLink:
    record: RecordRef = field(repr=False)
    reference: EvidenceRef = field(repr=False)


@dataclass(frozen=True)
class QueryRef:
    kind: str
    snapshot_id: str = field(repr=False)
    scope: Scope = field(repr=False)
    dimension: Dimension | None = None


@dataclass(frozen=True)
class InsightCandidate:
    candidate_id: str
    batch_id: str
    candidate_type: str
    title: str
    summary: str
    metrics: tuple[Metric, ...]
    conditions: tuple[Condition, ...]
    denominator: int
    rule_id: str
    rule_version: str
    query_ref: QueryRef = field(repr=False)
    source_refs: tuple[RecordRef, ...] = field(repr=False)
    evidence_refs: tuple[EvidenceLink, ...] = field(repr=False)
    status: str
    limitations: tuple[str, ...]
    suggested_next_action: str
    public_restrictions: tuple[str, ...]
    source_rule_fingerprint: str | None = None
    adapter_version: str = ADAPTER_VERSION
