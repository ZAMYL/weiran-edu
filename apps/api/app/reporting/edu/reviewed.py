"""Trusted saved-result adapter. No authentication, client JSON, prose or database reads."""

from collections import Counter
from dataclasses import replace
from fractions import Fraction

from app.analysis.edu.insights.models import ADAPTER_VERSION
from app.edu.review_models import AnalysisResult

from .adapters import _condition_key, _dimensions, conditions_from_dto, decimal_number, exact
from .models import (
    AnonymousStudent,
    Condition,
    ExportLimits,
    FindingMetric,
    ReportInput,
    ReviewedFinding,
    ReviewedFindings,
    Score,
    Source,
)
from .validation import count, group, identifier, require, source, validate

PROJECTION_VERSION = "edu.report.reviewed.v1"


def _conditions(report: ReportInput) -> tuple[Condition, ...]:
    require(report.scope_kind == "batch" and len(report.groups) <= 1, "reviewed_scope_required")
    return report.groups[0].conditions if report.groups else ()


def expected_metrics(report: ReportInput, finding: ReviewedFinding) -> tuple[FindingMetric, ...]:
    """Compare saved metrics to supplied trusted statistics, not a new analytics algorithm."""
    total = report.record_count
    require(finding.denominator == total, "finding_denominator_mismatch")
    require(finding.rule_version == ADAPTER_VERSION, "finding_rule_version_mismatch")
    if finding.candidate_type == "dimension_distribution":
        require(finding.rule_id == "edu.insight.distribution", "finding_rule_mismatch")
        dimension = next((d for d in report.dimensions if d.code == finding.dimension_code), None)
        require(dimension is not None, "finding_dimension_mismatch")
        require(
            finding.status == ("observed" if dimension.valid_count else "insufficient_data"),
            "finding_status_mismatch",
        )
        values = [
            FindingMetric(key, Fraction(value), "records", total, value)
            for key, value in (
                ("valid_count", dimension.valid_count),
                ("missing_count", dimension.missing_count),
                ("invalid_count", dimension.invalid_count),
            )
        ]
        values.extend(
            FindingMetric(key, value, "score", dimension.valid_count, dimension.valid_count)
            for key, value in (("mean", dimension.mean), ("median", dimension.median))
        )
        values.extend(
            FindingMetric(
                "frequency:" + str(cell.score),
                Fraction(cell.count),
                "records",
                dimension.valid_count,
                cell.count,
                cell.score,
            )
            for cell in dimension.distribution
        )
    else:
        require(
            finding.candidate_type == "ability_group"
            and finding.rule_id == "edu.insight.custom_group.v1"
            and finding.dimension_code is None
            and finding.status == "observed"
            and len(report.groups) == 1
            and report.groups[0].matched_count > 0,
            "finding_group_mismatch",
        )
        selected = report.groups[0]
        values = [
            FindingMetric(
                "matched_count",
                Fraction(selected.matched_count),
                "records",
                total,
                selected.matched_count,
            ),
            FindingMetric("matched_ratio", selected.ratio, "ratio", total, selected.matched_count),
            FindingMetric(
                "eligible_count",
                Fraction(selected.eligible_count),
                "records",
                total,
                selected.eligible_count,
            ),
            FindingMetric(
                "matched_among_eligible",
                selected.eligible_ratio,
                "ratio",
                selected.eligible_count,
                selected.matched_count,
            ),
            FindingMetric(
                "unevaluable_count",
                Fraction(selected.unevaluable_count),
                "records",
                total,
                selected.unevaluable_count,
            ),
        ]
    return tuple(sorted(values, key=lambda m: m.key))


def validate_reviewed(report: ReportInput) -> None:
    reviewed = report.reviewed
    require(isinstance(reviewed, ReviewedFindings), "typed_reviewed_findings_required")
    source(reviewed.source, report.source)
    identifier(reviewed.result_id)
    require(
        type(reviewed.result_version) is int and reviewed.result_version >= 1,
        "invalid_review_version",
    )
    require(reviewed.projection_version == PROJECTION_VERSION, "projection_version_mismatch")
    require(
        isinstance(reviewed.conditions, tuple)
        and _condition_key(reviewed.conditions) == _condition_key(_conditions(report)),
        "reviewed_conditions_mismatch",
    )
    require(
        isinstance(reviewed.findings, tuple) and len(reviewed.findings) <= 6,
        "reviewed_finding_limit",
    )
    require(
        all(isinstance(f, ReviewedFinding) for f in reviewed.findings),
        "typed_reviewed_finding_required",
    )
    require(
        len({f.candidate_id for f in reviewed.findings}) == len(reviewed.findings),
        "duplicate_saved_candidate",
    )
    for finding in reviewed.findings:
        identifier(finding.candidate_id)
        require(
            isinstance(finding.metrics, tuple)
            and all(isinstance(m, FindingMetric) for m in finding.metrics),
            "typed_finding_metrics_required",
        )
        expected = expected_metrics(report, finding)
        require(
            sorted(finding.metrics, key=lambda m: m.key) == list(expected),
            "finding_metric_mismatch",
        )
        # Equality alone accepts bool == int; counters must retain strict integer semantics.
        count(finding.denominator)
        for metric in finding.metrics:
            count(metric.denominator)
            count(metric.source_record_count)
            require(
                metric.value is None or isinstance(metric.value, Fraction),
                "exact_fraction_required",
            )


def _same_dimensions(left, right) -> bool:
    return sorted((replace(d, scale=None) for d in left), key=lambda d: d.code) == sorted(
        (replace(d, scale=None) for d in right), key=lambda d: d.code
    )


def _check_snapshot(report: ReportInput, result: AnalysisResult) -> tuple[Condition, ...]:
    snapshot = result.analysis
    query = snapshot.query
    require(query.snapshot_id == report.source.snapshot_id, "reviewed_snapshot_mismatch")
    conditions = conditions_from_dto(query.conditions) if query.conditions else ()
    require(
        _condition_key(conditions) == _condition_key(_conditions(report)),
        "reviewed_conditions_mismatch",
    )
    require(snapshot.statistics.rule_version == report.rule_version, "statistics_rule_mismatch")
    scales = {d.code: d.scale for d in report.dimensions}
    dimensions = _dimensions(
        snapshot.statistics, report.source, report.record_count, scales, kind="batch"
    )
    require(_same_dimensions(dimensions, report.dimensions), "saved_statistics_mismatch")
    require(snapshot.total_count == report.record_count, "saved_total_mismatch")
    count(snapshot.total_count)
    count(snapshot.matched_count)
    matched = report.groups[0].matched_count if conditions else report.record_count
    require(snapshot.matched_count == matched, "saved_group_count_mismatch")
    require(
        exact(snapshot.ratio)
        == (Fraction(matched, report.record_count) if report.record_count else None),
        "saved_ratio_mismatch",
    )
    require(
        isinstance(snapshot.members, tuple) and len(snapshot.members) == matched <= 5000,
        "incomplete_saved_members",
    )
    members = []
    for student in snapshot.members:
        identifier(student.student_id)
        require(
            isinstance(student.scores, tuple) and len(student.scores) == len(dimensions),
            "saved_member_dimension_mismatch",
        )
        cells = tuple(
            Score(s.dimension_code, decimal_number(s.score), s.evidence_id) for s in student.scores
        )
        require(
            {s.dimension_code for s in cells} == {d.code for d in dimensions},
            "saved_member_dimension_mismatch",
        )
        for cell in cells:
            identifier(cell.evidence_id)
        members.append(AnonymousStudent(report.source, student.student_id, cells))
    require(len({s.student_id for s in members}) == len(members), "duplicate_saved_member")
    evidence = [c.evidence_id for s in members for c in s.scores]
    require(len(set(evidence)) == len(evidence), "duplicate_evidence_reference")
    if conditions:
        selected = report.groups[0]
        require(
            snapshot.group_statistics is not None and selected.statistics is not None,
            "saved_group_statistics_required",
        )
        require(
            snapshot.group_statistics.rule_version == report.rule_version == selected.rule_version,
            "statistics_rule_mismatch",
        )
        group_dims = _dimensions(
            snapshot.group_statistics, report.source, matched, scales, kind="matched_group"
        )
        require(
            _same_dimensions(group_dims, selected.statistics), "saved_group_statistics_mismatch"
        )
        group(replace(selected, members=tuple(members)), report)
        if selected.members is not None:
            require(
                {s.student_id: set(s.scores) for s in members}
                == {s.student_id: set(s.scores) for s in selected.members},
                "saved_member_source_mismatch",
            )
    else:
        require(snapshot.group_statistics is None, "unexpected_group_statistics")
        for dimension in dimensions:
            actual = Counter(
                c.value for s in members for c in s.scores if c.dimension_code == dimension.code
            )
            require(
                actual == Counter({c.score: c.count for c in dimension.distribution}),
                "saved_member_statistics_mismatch",
            )
    return tuple(sorted(conditions, key=lambda c: (c.dimension_code, c.operator, c.threshold)))


def with_reviewed_findings(
    report: ReportInput,
    result: AnalysisResult,
    *,
    expected_result_id: str,
    expected_version: int,
) -> ReportInput:
    """Server-only projection AFTER permission/current-version/source checks by ResultService.

    A typed DTO is not proof of persistence or authentication. Never expose this input to clients.
    Free titles, descriptions, notes, reviewers, original text and member IDs are not projected.
    """
    require(isinstance(result, AnalysisResult), "saved_analysis_result_required")
    validate(report, ExportLimits(), False, None)
    identifier(expected_result_id)
    require(result.result_id == expected_result_id, "saved_result_id_mismatch")
    require(
        type(expected_version) is int
        and expected_version >= 1
        and type(result.version) is int
        and result.version == expected_version,
        "saved_result_version_mismatch",
    )
    require(result.audience == "authorized_teacher_only", "reviewed_audience_mismatch")
    source(Source(result.batch_id, result.snapshot_id), report.source)
    conditions = _check_snapshot(report, result)
    candidates = result.analysis.candidates
    require(
        isinstance(candidates, tuple)
        and len(candidates) <= 6
        and isinstance(result.findings, tuple)
        and len(result.findings) == len(candidates),
        "saved_candidate_limit",
    )
    by_id = {c.candidate_id: c for c in candidates}
    require(len(by_id) == len(candidates), "duplicate_saved_candidate")
    seen, output = set(), []
    for saved in result.findings:
        candidate, decision = saved.candidate, saved.teacher_review
        require(
            candidate.candidate_id in by_id and candidate == by_id[candidate.candidate_id],
            "candidate_not_in_saved_result",
        )
        require(
            decision.candidate_id == candidate.candidate_id and decision.candidate_id not in seen,
            "saved_decision_mismatch",
        )
        seen.add(decision.candidate_id)
        require(decision.status in {"pending", "confirmed", "dismissed"}, "invalid_saved_decision")
        source(Source(candidate.batch_id, candidate.snapshot_id), report.source)
        require(candidate.public_display_allowed is False, "reviewed_audience_mismatch")
        card_conditions = conditions_from_dto(candidate.conditions) if candidate.conditions else ()
        required_conditions = conditions if candidate.candidate_type == "ability_group" else ()
        require(
            _condition_key(card_conditions) == _condition_key(required_conditions),
            "candidate_conditions_mismatch",
        )
        require(
            query_dimensions_allowed(result, candidate.candidate_type),
            "candidate_not_in_saved_query",
        )
        require(
            isinstance(candidate.metrics, tuple) and len(candidate.metrics) <= 406,
            "finding_metric_limit",
        )
        metrics = tuple(
            FindingMetric(
                m.key,
                exact(m.value),
                m.unit,
                m.denominator,
                m.source_record_count,
                exact(m.at_score) if m.at_score is not None else None,
            )
            for m in candidate.metrics
        )
        projected = ReviewedFinding(
            candidate.candidate_id,
            candidate.candidate_type,
            candidate.dimension_code,
            candidate.denominator,
            candidate.rule_id,
            candidate.rule_version,
            candidate.status,
            tuple(sorted(metrics, key=lambda m: m.key)),
        )
        # Check even pending/dismissed cards for corrupt saved metadata, never silently bless it.
        checked = ReviewedFindings(
            report.source, result.result_id, result.version, conditions, (projected,)
        )
        validate_reviewed(replace(report, reviewed=checked))
        if decision.status == "confirmed":
            output.append(projected)
    reviewed = ReviewedFindings(
        report.source,
        result.result_id,
        result.version,
        conditions,
        tuple(sorted(output, key=lambda f: f.candidate_id)),
    )
    answer = replace(report, reviewed=reviewed)
    validate(answer, ExportLimits(), False, None)
    return answer


def query_dimensions_allowed(result: AnalysisResult, candidate_type: str) -> bool:
    return (
        candidate_type != "dimension_distribution"
        or result.analysis.query.include_dimensions is True
    )
