"""Internal whitelist inputs. Upstream code owns authentication and source verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.edu.discovery_models import DiscoveryDetail


class ReportError(ValueError):
    """Only fixed reason codes cross the reporting boundary."""


@dataclass(frozen=True)
class Source:
    batch_id: str
    snapshot_id: str


@dataclass(frozen=True)
class Scale:
    minimum: Fraction
    maximum: Fraction
    step: Fraction
    rubric_version: str | None = None


@dataclass(frozen=True)
class Frequency:
    score: Fraction
    count: int


@dataclass(frozen=True)
class DimensionReport:
    source: Source
    code: str
    valid_count: int
    missing_count: int
    invalid_count: int
    mean: Fraction | None
    median: Fraction | None
    minimum: Fraction | None
    maximum: Fraction | None
    distribution: tuple[Frequency, ...]
    scale: Scale | None = None


@dataclass(frozen=True)
class Condition:
    dimension_code: str
    operator: Literal["lt", "lte", "eq", "gte", "gt"]
    threshold: Fraction


@dataclass(frozen=True)
class Score:
    dimension_code: str
    value: Fraction
    evidence_id: str


@dataclass(frozen=True)
class AnonymousStudent:
    source: Source
    student_id: str = field(repr=False)
    scores: tuple[Score, ...] = field(repr=False)


@dataclass(frozen=True)
class GroupReport:
    source: Source
    group_id: str
    conditions: tuple[Condition, ...]
    matched_count: int
    total_count: int
    eligible_count: int
    unevaluable_count: int
    ratio: Fraction | None
    eligible_ratio: Fraction | None
    rule_version: str
    members: tuple[AnonymousStudent, ...] | None = field(default=None, repr=False)
    statistics: tuple[DimensionReport, ...] | None = None


@dataclass(frozen=True)
class FindingMetric:
    key: str
    value: Fraction | None
    unit: Literal["records", "score", "ratio"]
    denominator: int
    source_record_count: int
    at_score: Fraction | None = None


@dataclass(frozen=True)
class ReviewedFinding:
    candidate_id: str
    candidate_type: Literal["dimension_distribution", "ability_group"]
    dimension_code: str | None
    denominator: int
    rule_id: str
    rule_version: str
    status: Literal["observed", "insufficient_data"]
    metrics: tuple[FindingMetric, ...]


@dataclass(frozen=True)
class ReviewedFindings:
    """Server-derived whitelist, never a client confirmation or permission token."""

    source: Source
    result_id: str
    result_version: int
    conditions: tuple[Condition, ...]
    findings: tuple[ReviewedFinding, ...]
    projection_version: str = "edu.report.reviewed.v1"


@dataclass(frozen=True)
class ReportInput:
    source: Source
    generated_at: datetime
    record_count: int
    dimensions: tuple[DimensionReport, ...]
    rule_version: str
    groups: tuple[GroupReport, ...] = ()
    scope_kind: Literal["batch", "authorized_subset"] = "batch"
    synthetic: bool = False
    reviewed: ReviewedFindings | None = None
    discoveries: tuple[DiscoveryDetail, ...] = ()


@dataclass(frozen=True)
class GroupGrant:
    group_id: str
    student_ids: frozenset[str] = field(repr=False)


@dataclass(frozen=True)
class DetailAuthorization:
    """Trusted server assertion, NOT a token, login check, or permission resolver."""

    source: Source
    grants: tuple[GroupGrant, ...] = field(repr=False)
    purpose: Literal["teacher_internal"] = "teacher_internal"


@dataclass(frozen=True)
class ExportLimits:
    max_rows_per_sheet: int = 25_000
    max_total_cells: int = 250_000
    max_file_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True)
class ReportFiles:
    xlsx: bytes = field(repr=False)
    markdown: str = field(repr=False)
