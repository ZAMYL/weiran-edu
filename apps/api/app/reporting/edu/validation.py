"""Reject inconsistent snapshots, counts, full-group claims and unsafe identifiers."""

import re
from collections import Counter
from datetime import datetime
from fractions import Fraction
from operator import eq, ge, gt, le, lt

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
    ReportInput,
    Scale,
    Score,
    Source,
)

COMPARISONS = {"lt": lt, "lte": le, "eq": eq, "gte": ge, "gt": gt}


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ReportError(code)


def identifier(value: object) -> None:
    require(
        isinstance(value, str)
        and bool(
            re.fullmatch(
                r"(?:[A-Za-z][A-Za-z0-9_.-]{0,95}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})",
                value,
            )
        ),
        "opaque_identifier_required",
    )
    require(
        not value.lower().startswith(("wedu_", "sk-", "ghp_", "gho_", "bearer")),
        "credential_like_identifier",
    )


def count(value: object) -> None:
    require(type(value) is int and 0 <= value <= 1_000_000, "invalid_count")


def numeric(value: object) -> None:
    require(
        isinstance(value, Fraction)
        and abs(value.numerator) <= 10**12
        and value.denominator <= 10**12,
        "exact_fraction_required",
    )


def source(value: Source, expected: Source) -> None:
    require(isinstance(value, Source) and value == expected, "source_mismatch")
    identifier(value.batch_id)
    identifier(value.snapshot_id)


def score(value: Fraction, scale: Scale | None) -> None:
    numeric(value)
    if scale:
        require(scale.minimum <= value <= scale.maximum, "score_outside_scale")
        require(((value - scale.minimum) / scale.step).denominator == 1, "score_off_step")


def dimension(item: DimensionReport, expected: Source, total: int) -> None:
    require(isinstance(item, DimensionReport), "typed_dimension_required")
    source(item.source, expected)
    require(
        isinstance(item.code, str) and bool(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item.code)),
        "invalid_dimension_code",
    )
    for value in (item.valid_count, item.missing_count, item.invalid_count):
        count(value)
    require(
        item.valid_count + item.missing_count + item.invalid_count == total,
        "dimension_sample_mismatch",
    )
    if item.scale:
        require(isinstance(item.scale, Scale), "typed_scale_required")
        for value in (item.scale.minimum, item.scale.maximum, item.scale.step):
            numeric(value)
        require(item.scale.minimum < item.scale.maximum and item.scale.step > 0, "invalid_scale")
        if item.scale.rubric_version is not None:
            identifier(item.scale.rubric_version)
    require(
        isinstance(item.distribution, tuple) and len(item.distribution) <= 10_000,
        "distribution_limit",
    )
    require(
        all(isinstance(cell, Frequency) for cell in item.distribution), "typed_frequency_required"
    )
    for cell in item.distribution:
        numeric(cell.score)
    bins = sorted(item.distribution, key=lambda cell: cell.score)
    require(len({cell.score for cell in bins}) == len(bins), "duplicate_frequency")
    for cell in bins:
        score(cell.score, item.scale)
        count(cell.count)
        require(cell.count > 0, "positive_frequency_required")
    require(sum(cell.count for cell in bins) == item.valid_count, "frequency_count_mismatch")
    actual = (item.mean, item.median, item.minimum, item.maximum)
    if not item.valid_count:
        require(actual == (None, None, None, None) and not bins, "empty_statistics_mismatch")
        return
    for value in actual:
        numeric(value)
    # Integrity checks on already supplied statistics, not a new analysis endpoint.
    mean = sum((cell.score * cell.count for cell in bins), Fraction()) / item.valid_count
    middle = ((item.valid_count - 1) // 2, item.valid_count // 2)
    positions, seen = [], 0
    for cell in bins:
        positions.extend(cell.score for index in middle if seen <= index < seen + cell.count)
        seen += cell.count
    require(
        actual == (mean, sum(positions, Fraction()) / 2, bins[0].score, bins[-1].score),
        "statistics_frequency_mismatch",
    )


def dimensions(items: tuple[DimensionReport, ...], expected: Source, total: int) -> None:
    require(isinstance(items, tuple) and len(items) <= 32, "dimension_limit")
    require(all(isinstance(item, DimensionReport) for item in items), "typed_dimension_required")
    require(len({item.code for item in items}) == len(items), "duplicate_dimension")
    for item in items:
        dimension(item, expected, total)


def group(item: GroupReport, report: ReportInput) -> None:
    require(isinstance(item, GroupReport), "typed_group_required")
    source(item.source, report.source)
    identifier(item.group_id)
    identifier(item.rule_version)
    for value in (
        item.matched_count,
        item.total_count,
        item.eligible_count,
        item.unevaluable_count,
    ):
        count(value)
    require(
        item.total_count == report.record_count
        and item.eligible_count + item.unevaluable_count == item.total_count
        and item.matched_count <= item.eligible_count,
        "group_denominator_mismatch",
    )
    for value in (item.ratio, item.eligible_ratio):
        if value is not None:
            numeric(value)
    require(
        item.ratio == (Fraction(item.matched_count, item.total_count) if item.total_count else None)
        and item.eligible_ratio
        == (Fraction(item.matched_count, item.eligible_count) if item.eligible_count else None),
        "group_ratio_mismatch",
    )
    catalog = {d.code: d for d in report.dimensions}
    require(
        isinstance(item.conditions, tuple) and 1 <= len(item.conditions) <= 2, "condition_count"
    )
    require(all(isinstance(c, Condition) for c in item.conditions), "typed_condition_required")
    require(
        len({c.dimension_code for c in item.conditions}) == len(item.conditions),
        "duplicate_condition_dimension",
    )
    for condition in item.conditions:
        require(
            condition.dimension_code in catalog and condition.operator in COMPARISONS,
            "invalid_condition_mapping",
        )
        score(condition.threshold, catalog[condition.dimension_code].scale)
    if item.statistics is not None:
        dimensions(item.statistics, report.source, item.matched_count)
        require({d.code for d in item.statistics} == set(catalog), "group_dimension_mismatch")
        require(
            all(d.scale == catalog[d.code].scale for d in item.statistics), "group_scale_mismatch"
        )
    if item.members is None:
        return
    require(
        isinstance(item.members, tuple)
        and len(item.members) <= 5000
        and len(item.members) == item.matched_count,
        "incomplete_group_members",
    )
    require(all(isinstance(row, AnonymousStudent) for row in item.members), "typed_member_required")
    require(
        len({row.student_id for row in item.members}) == len(item.members), "duplicate_student_id"
    )
    for row in item.members:
        source(row.source, report.source)
        identifier(row.student_id)
        require(
            isinstance(row.scores, tuple) and all(isinstance(s, Score) for s in row.scores),
            "typed_scores_required",
        )
        require(
            len({s.dimension_code for s in row.scores}) == len(row.scores)
            and {s.dimension_code for s in row.scores} == set(catalog),
            "member_dimension_mismatch",
        )
        for cell in row.scores:
            score(cell.value, catalog[cell.dimension_code].scale)
            identifier(cell.evidence_id)
        values = {s.dimension_code: s.value for s in row.scores}
        require(
            all(
                COMPARISONS[c.operator](values[c.dimension_code], c.threshold)
                for c in item.conditions
            ),
            "member_does_not_match_conditions",
        )

    evidence_ids = [cell.evidence_id for row in item.members for cell in row.scores]
    require(len(set(evidence_ids)) == len(evidence_ids), "duplicate_evidence_reference")
    for code, summary in catalog.items():
        actual = Counter(
            cell.value for row in item.members for cell in row.scores if cell.dimension_code == code
        )
        available = {cell.score: cell.count for cell in summary.distribution}
        require(
            all(n <= available.get(value, 0) for value, n in actual.items()),
            "group_members_outside_summary",
        )
        if item.statistics is not None:
            detail = next(d for d in item.statistics if d.code == code)
            require(
                actual == Counter({c.score: c.count for c in detail.distribution}),
                "group_statistics_member_mismatch",
            )


def validate(
    report: ReportInput,
    limits: ExportLimits,
    include_details: bool,
    authorization: DetailAuthorization | None,
) -> None:
    require(
        isinstance(report, ReportInput) and isinstance(limits, ExportLimits),
        "typed_report_required",
    )
    for name, ceiling in (
        ("max_rows_per_sheet", 25_000),
        ("max_total_cells", 250_000),
        ("max_file_bytes", 8 * 1024 * 1024),
    ):
        value = getattr(limits, name)
        require(type(value) is int and 1 <= value <= ceiling, "invalid_export_limit")
    source(report.source, report.source)
    require(
        isinstance(report.generated_at, datetime)
        and report.generated_at.tzinfo is not None
        and report.generated_at.utcoffset() is not None,
        "aware_generation_time_required",
    )
    require(report.scope_kind in {"batch", "authorized_subset"}, "invalid_scope")
    identifier(report.rule_version)
    require(type(report.synthetic) is bool, "boolean_origin_required")
    count(report.record_count)
    dimensions(report.dimensions, report.source, report.record_count)
    require(isinstance(report.groups, tuple) and len(report.groups) <= 20, "group_limit")
    require(all(isinstance(g, GroupReport) for g in report.groups), "typed_group_required")
    require(len({g.group_id for g in report.groups}) == len(report.groups), "duplicate_group")
    for item in report.groups:
        group(item, report)
    if report.reviewed is not None:
        # Lazy import keeps the existing statistics adapter independent of this extension.
        from .reviewed import validate_reviewed

        validate_reviewed(report)
    require(type(include_details) is bool, "boolean_detail_option_required")
    if not include_details:
        return
    require(
        isinstance(authorization, DetailAuthorization)
        and authorization.purpose == "teacher_internal",
        "detail_authorization_required",
    )
    source(authorization.source, report.source)
    require(
        isinstance(authorization.grants, tuple)
        and all(isinstance(g, GroupGrant) for g in authorization.grants),
        "typed_grants_required",
    )
    grants = {g.group_id: g.student_ids for g in authorization.grants}
    require(len(grants) == len(authorization.grants), "duplicate_grant")
    for item in report.groups:
        require(item.members is not None, "incomplete_group_members")
        require(
            item.group_id in grants
            and isinstance(grants[item.group_id], frozenset)
            and grants[item.group_id] == frozenset(row.student_id for row in item.members),
            "group_access_not_authorized",
        )
