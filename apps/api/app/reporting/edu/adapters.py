"""Read-only adapters for merged Statistics/GroupResult; never import PR #19 DTOs."""

import hashlib
import json
from datetime import datetime
from decimal import DecimalException
from fractions import Fraction
from math import isclose, isfinite

from app.edu.analysis_models import FilterCondition, GroupResult, Statistics, StatValue
from app.edu.models import Rubric
from app.importing.edu.types import DIMENSIONS

from .models import (
    AnonymousStudent,
    Condition,
    DimensionReport,
    ExportLimits,
    Frequency,
    GroupReport,
    ReportError,
    ReportInput,
    Scale,
    Score,
    Source,
)
from .validation import count, numeric, require, validate


def decimal_number(value: object) -> Fraction:
    require(
        type(value) in (int, float) and abs(value) <= 10**12 and isfinite(value),
        "finite_numeric_input_required",
    )
    result = Fraction(str(value))
    numeric(result)
    return result


def exact(value: StatValue) -> Fraction | None:
    if value.exact is None:
        require(value.value is None, "numeric_hint_without_exact_value")
        return None
    require(
        type(value.exact.numerator) is int
        and type(value.exact.denominator) is int
        and value.exact.denominator > 0,
        "invalid_exact_value",
    )
    result = Fraction(value.exact.numerator, value.exact.denominator)
    numeric(result)
    require(
        type(value.value) in (int, float)
        and abs(value.value) <= 10**12
        and isfinite(value.value)
        and isclose(value.value, float(result), rel_tol=1e-12, abs_tol=1e-12),
        "numeric_hint_mismatch",
    )
    # Free-text display is intentionally ignored, not copied into a report.
    return result


def _dimensions(
    statistics: Statistics,
    source: Source,
    total: int,
    scales: dict[str, Scale],
    *,
    kind: str,
) -> tuple[DimensionReport, ...]:
    require(isinstance(statistics, Statistics), "statistics_dto_required")
    require(
        Source(statistics.batch_id, statistics.snapshot_id) == source
        and statistics.scope.kind == kind
        and statistics.scope.record_count == total,
        "statistics_scope_mismatch",
    )
    count(statistics.scope.record_count)
    require(len(statistics.dimensions) <= 32, "dimension_limit")
    output = []
    for item in statistics.dimensions:
        require(
            item.dimension_code in {spec.code for spec in DIMENSIONS}, "invalid_dimension_mapping"
        )
        require(len(item.distribution) <= 10_000, "distribution_limit")
        bins = []
        for cell in item.distribution:
            score = decimal_number(cell.score)
            require(cell.denominator == item.valid_count, "frequency_denominator_mismatch")
            ratio = exact(cell.ratio)
            require(
                ratio == (Fraction(cell.count, cell.denominator) if cell.denominator else None),
                "frequency_ratio_mismatch",
            )
            bins.append(Frequency(score, cell.count))
        output.append(
            DimensionReport(
                source,
                item.dimension_code,
                item.valid_count,
                item.missing_count,
                item.invalid_count,
                exact(item.mean),
                exact(item.median),
                exact(item.minimum),
                exact(item.maximum),
                tuple(bins),
                scales.get(item.dimension_code),
            )
        )
    return tuple(output)


def conditions_from_dto(items) -> tuple[Condition, ...]:
    require(isinstance(items, tuple) and 1 <= len(items) <= 2, "condition_count")
    result = []
    for item in items:
        try:
            threshold = FilterCondition.numeric_threshold(item.value)
        except (ValueError, DecimalException):
            raise ReportError("invalid_filter_condition") from None
        result.append(Condition(item.dimension_code, item.operator, Fraction(threshold)))
    return tuple(result)


def _condition_key(conditions: tuple[Condition, ...]) -> tuple:
    return tuple(sorted((c.dimension_code, c.operator, str(c.threshold)) for c in conditions))


def _collect(
    pages: tuple[GroupResult, ...],
    source: Source,
    total: int,
    codes: set[str],
    scales: dict[str, Scale],
) -> GroupReport:
    require(isinstance(pages, tuple) and 1 <= len(pages) <= 5000, "group_pages_required")
    require(all(isinstance(page, GroupResult) for page in pages), "group_dto_required")
    ordered = sorted(pages, key=lambda page: page.page.offset)
    first = ordered[0]
    count(first.matched_count)
    require(first.matched_count <= 5000, "member_limit")
    conditions = conditions_from_dto(first.conditions)
    key = _condition_key(conditions)
    group_id = "group-" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]
    members, cursor, group_stats = [], 0, None
    if first.statistics is not None:
        require(first.statistics.rule_version == first.rule_version, "group_rule_mismatch")
        group_stats = _dimensions(
            first.statistics, source, first.matched_count, scales, kind="matched_group"
        )
    for page in ordered:
        require(Source(page.batch_id, page.snapshot_id) == source, "group_source_mismatch")
        require(page.total_count == total, "group_denominator_mismatch")
        other_key = _condition_key(conditions_from_dto(page.conditions))
        require(
            other_key == key and page.rule_version == first.rule_version,
            "group_page_definition_mismatch",
        )
        require(
            (
                page.matched_count,
                page.eligible_count,
                page.unevaluable_count,
                page.ratio,
                page.matched_among_eligible_ratio,
            )
            == (
                first.matched_count,
                first.eligible_count,
                first.unevaluable_count,
                first.ratio,
                first.matched_among_eligible_ratio,
            ),
            "group_page_count_mismatch",
        )
        count(page.page.offset)
        count(page.page.limit)
        count(page.page.returned)
        require(
            1 <= page.page.limit <= 100
            and page.page.offset == cursor
            and page.page.returned == len(page.items) <= page.page.limit
            and type(page.page.has_more) is bool,
            "incomplete_group_pages",
        )
        require(
            bool(page.items) or (first.matched_count == 0 and len(ordered) == 1),
            "incomplete_group_pages",
        )
        current_stats = (
            None
            if page.statistics is None
            else _dimensions(
                page.statistics,
                source,
                first.matched_count,
                scales,
                kind="matched_group",
            )
        )
        require(current_stats == group_stats, "group_statistics_page_mismatch")
        for student in page.items:
            require(
                len({cell.dimension_code for cell in student.scores}) == len(student.scores)
                and all(
                    cell.dimension_code in {spec.code for spec in DIMENSIONS}
                    for cell in student.scores
                ),
                "member_dimension_mismatch",
            )
            scores = []
            for cell in student.scores:
                if cell.dimension_code in codes:
                    value = decimal_number(cell.score)
                    require(
                        1 <= value <= 5 and (value * 100).denominator == 1,
                        "invalid_formal_member_score",
                    )
                    scores.append(Score(cell.dimension_code, value, cell.evidence_id))
            # display_label, raw text, source locators and any extra fields are never copied.
            members.append(AnonymousStudent(source, student.student_id, tuple(scores)))
        cursor += len(page.items)
        require(page.page.has_more == (cursor < first.matched_count), "incomplete_group_pages")
    require(cursor == first.matched_count, "incomplete_group_pages")
    ratio = Fraction(first.matched_count, total) if total else None
    eligible_ratio = (
        Fraction(first.matched_count, first.eligible_count) if first.eligible_count else None
    )
    require(
        type(first.ratio) in (int, float)
        and isfinite(first.ratio)
        and ratio is not None
        and isclose(first.ratio, float(ratio), abs_tol=1e-12),
        "group_ratio_mismatch",
    )
    require(
        (eligible_ratio is None and first.matched_among_eligible_ratio is None)
        or (
            eligible_ratio is not None
            and type(first.matched_among_eligible_ratio) in (int, float)
            and isfinite(first.matched_among_eligible_ratio)
            and isclose(first.matched_among_eligible_ratio, float(eligible_ratio), abs_tol=1e-12)
        ),
        "group_ratio_mismatch",
    )
    return GroupReport(
        source,
        group_id,
        conditions,
        first.matched_count,
        total,
        first.eligible_count,
        first.unevaluable_count,
        ratio,
        eligible_ratio,
        first.rule_version,
        tuple(members),
        group_stats,
    )


def from_statistics(
    statistics: Statistics,
    *,
    generated_at: datetime,
    rubric: Rubric | None = None,
    group_pages: tuple[tuple[GroupResult, ...], ...] = (),
) -> ReportInput:
    """Caller supplies only authorized current snapshots and all pages, not HTTP responses."""
    require(isinstance(statistics, Statistics), "statistics_dto_required")
    source = Source(statistics.batch_id, statistics.snapshot_id)
    total = statistics.scope.record_count
    scales = {}
    if rubric is not None:
        require(
            isinstance(rubric, Rubric) and Source(rubric.batch_id, rubric.snapshot_id) == source,
            "rubric_source_mismatch",
        )
        codes = [item.dimension_code for item in rubric.criteria]
        require(
            len(set(codes)) == len(codes) and set(codes) == {spec.code for spec in DIMENSIONS},
            "invalid_rubric_mapping",
        )
        policy = rubric.score_policy
        scale = Scale(
            decimal_number(policy.minimum),
            decimal_number(policy.maximum),
            decimal_number(policy.step),
            rubric.rubric_version,
        )
        scales = {code: scale for code in codes}
    dims = _dimensions(statistics, source, total, scales, kind="batch")
    require(isinstance(group_pages, tuple) and len(group_pages) <= 20, "group_limit")
    groups = tuple(
        _collect(pages, source, total, {d.code for d in dims}, scales) for pages in group_pages
    )
    result = ReportInput(source, generated_at, total, dims, statistics.rule_version, groups)
    validate(result, ExportLimits(), False, None)
    return result
