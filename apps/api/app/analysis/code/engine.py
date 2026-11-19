"""Orchestration: validate, extract, associate, and emit pattern candidates."""

from dataclasses import replace
from fractions import Fraction

from .association import AssociationState, FeatureAssociation, build_association
from .contract import (
    CodeAnalysisError,
    Language,
    SourceRef,
    SubmissionRecord,
    TestPointOutcome,
    TestPointResult,
    Verdict,
    reference_of,
    require_id,
)
from .explanation import ObservedFact
from .features import FeatureId, is_present
from .languages import extract
from .results import (
    Analysis,
    OutcomeCounts,
    PatternCandidate,
    SubmissionFeatures,
    TestPointAggregate,
)

_LIMITS: tuple[str, ...] = (
    "Association is a measured co-occurrence, not causation; candidates are unverified.",
    "Count features are binarised for association; max_loop_nesting presence means depth >= 2.",
    "direct_self_recursion detects direct self-calls only, never mutual recursion.",
    "Python features are AST-backed (reliable); C++ features are lexical (tentative).",
    "Submissions with missing source, unparseable code, or non-success/failure verdicts "
    "are excluded from associations and counted, never imputed.",
    "Analysis is scoped to one problem; cross-problem pooling is out of scope.",
)


def analyze(
    batch_id: str,
    problem_id: str,
    submissions,
    *,
    minimum_samples: int = 2,
) -> Analysis:
    """Analyse one problem's submissions into features, associations and candidates.

    ``submissions`` is a collection of :class:`SubmissionRecord`. Structural
    ambiguity (duplicate submissions, inconsistent identity, duplicate test
    points, out-of-scope records) is rejected rather than silently repaired.
    """

    require_id(batch_id, "nonempty_batch_id_required")
    require_id(problem_id, "nonempty_problem_id_required")
    records = _validate(batch_id, problem_id, submissions)

    submissions_features = tuple(
        SubmissionFeatures(
            reference_of(record),
            record.verdict,
            extract(record.language, record.source_code),
            record.test_points,
        )
        for record in records
    )

    outcomes = _count_outcomes(records)
    associations = tuple(
        _associate(feature, submissions_features, minimum_samples) for feature in FeatureId
    )
    test_points = _testpoint_aggregates(submissions_features)
    candidates = _candidates(associations, submissions_features)

    return Analysis(
        batch_id,
        problem_id,
        submissions_features,
        outcomes,
        associations,
        test_points,
        candidates,
        _LIMITS,
    )


def _validate(batch_id: str, problem_id: str, submissions) -> tuple[SubmissionRecord, ...]:
    if isinstance(submissions, (str, bytes)):
        raise CodeAnalysisError("submissions_must_be_a_collection")
    records = tuple(submissions)
    seen_submissions: set[tuple[str, str, str]] = set()
    identity: dict[str, str] = {}
    validated = []
    for record in records:
        if not isinstance(record, SubmissionRecord):
            raise CodeAnalysisError("typed_submission_record_required")
        if record.batch_id != batch_id or record.problem_id != problem_id:
            raise CodeAnalysisError("submission_outside_scope")
        for identifier in (
            record.submission_id,
            record.student_id,
            record.source.source_id,
            record.source.location,
        ):
            require_id(identifier, "nonempty_identifier_required")
        if not isinstance(record.language, Language):
            raise CodeAnalysisError("typed_language_required")
        if not isinstance(record.source_code, str):
            raise CodeAnalysisError("source_code_must_be_a_string")
        if not isinstance(record.verdict, Verdict):
            raise CodeAnalysisError("typed_verdict_required")
        if not isinstance(record.source, SourceRef):
            raise CodeAnalysisError("typed_source_reference_required")
        record = replace(record, test_points=_validate_test_points(record.test_points))
        previous_student = identity.get(record.submission_id)
        if previous_student is not None and previous_student != record.student_id:
            raise CodeAnalysisError("inconsistent_submission_identity")
        key = (batch_id, problem_id, record.submission_id)
        if key in seen_submissions:
            raise CodeAnalysisError("duplicate_submission")
        identity[record.submission_id] = record.student_id
        seen_submissions.add(key)
        validated.append(record)
    return tuple(sorted(validated, key=lambda item: item.submission_id))


def _validate_test_points(test_points) -> tuple:
    if not isinstance(test_points, (tuple, list)):
        raise CodeAnalysisError("test_points_must_be_a_collection")
    seen: set[str] = set()
    points = []
    for point in test_points:
        if not isinstance(point, TestPointResult):
            raise CodeAnalysisError("typed_test_point_required")
        require_id(point.testpoint_id, "nonempty_testpoint_id_required")
        if not isinstance(point.outcome, TestPointOutcome):
            raise CodeAnalysisError("typed_testpoint_outcome_required")
        if point.testpoint_id in seen:
            raise CodeAnalysisError("duplicate_testpoint_in_submission")
        seen.add(point.testpoint_id)
        points.append(point)
    return tuple(points)


def _count_outcomes(records: tuple[SubmissionRecord, ...]) -> OutcomeCounts:
    passed = sum(record.verdict.is_success for record in records)
    failed = sum(record.verdict.is_failure for record in records)
    return OutcomeCounts(passed, failed, len(records) - passed - failed)


def _associate(
    feature: FeatureId, submissions: tuple[SubmissionFeatures, ...], minimum_samples: int
) -> FeatureAssociation:
    present = []
    failed = []
    for submission in submissions:
        if submission.verdict.is_success or submission.verdict.is_failure:
            present.append(is_present(submission.features, feature))
            failed.append(submission.verdict.is_failure)
    return build_association(feature, present, failed, minimum_samples=minimum_samples)


def _testpoint_aggregates(
    submissions: tuple[SubmissionFeatures, ...],
) -> tuple[TestPointAggregate, ...]:
    counts: dict[str, list[int]] = {}
    for submission in submissions:
        for point in submission.test_points:
            slot = counts.setdefault(point.testpoint_id, [0, 0, 0])
            if point.outcome is TestPointOutcome.PASSED:
                slot[0] += 1
            elif point.outcome is TestPointOutcome.FAILED:
                slot[1] += 1
            else:
                slot[2] += 1
    return tuple(
        TestPointAggregate(testpoint_id, *counts[testpoint_id]) for testpoint_id in sorted(counts)
    )


def _candidates(
    associations: tuple[FeatureAssociation, ...],
    submissions: tuple[SubmissionFeatures, ...],
) -> tuple[PatternCandidate, ...]:
    candidates = []
    for association in associations:
        if association.state is not AssociationState.COMPUTABLE:
            continue
        difference = association.contingency.failure_rate_difference
        if difference is None or difference == 0:
            continue
        contingency = association.contingency
        evidence = tuple(
            submission.reference.source
            for submission in submissions
            if submission.verdict.is_failure
            and is_present(submission.features, association.feature) is True
        )
        observed = (
            ObservedFact(
                f"{contingency.feature_total} judged submission(s) have "
                f"'{association.feature.value}' present; {contingency.no_feature_total} do not."
            ),
            ObservedFact(
                f"Failure rate with feature present {_ratio(contingency.feature_failure_rate)}; "
                f"without {_ratio(contingency.no_feature_failure_rate)}."
            ),
        )
        limits = (
            "Statistical association, not a confirmed root cause; a human or a later agent "
            "must verify before interpreting.",
        )
        candidates.append(
            PatternCandidate(
                association.feature,
                association,
                observed,
                (),
                limits,
                evidence,
            )
        )
    return tuple(candidates)


def _ratio(value: Fraction | None) -> str:
    return "—" if value is None else f"{value.numerator}/{value.denominator}"
