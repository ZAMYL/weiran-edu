from collections.abc import Callable
from fractions import Fraction

from app.analysis.code import (
    AssociationState,
    FeatureId,
    Hypothesis,
    HypothesisStatus,
    Language,
    SubmissionRecord,
    Verdict,
    analyze,
    extractor_for,
)

_RECURSIVE = "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\n"
_LOOP = "for i in range(10):\n    print(i)\n"
_NESTED = "for i in range(3):\n    for j in range(3):\n        print(i, j)\n"


def _recursion_scenario(make_submission: Callable[..., SubmissionRecord]):
    records = [
        make_submission("1", _RECURSIVE, Verdict.WRONG_ANSWER),
        make_submission("2", _RECURSIVE, Verdict.WRONG_ANSWER),
        make_submission("3", _RECURSIVE, Verdict.WRONG_ANSWER),
        make_submission("4", _RECURSIVE, Verdict.ACCEPTED),
        make_submission("5", _LOOP, Verdict.WRONG_ANSWER),
        make_submission("6", _LOOP, Verdict.ACCEPTED),
        make_submission("7", _LOOP, Verdict.ACCEPTED),
        make_submission("8", _LOOP, Verdict.ACCEPTED),
    ]
    return analyze("batch-1", "problem-1", records)


def test_recursion_associates_with_failure(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    result = _recursion_scenario(make_submission)
    association = result.association_for(FeatureId.DIRECT_SELF_RECURSION)
    assert association is not None
    assert association.state is AssociationState.COMPUTABLE
    assert association.contingency.feature_failed == 3
    assert association.contingency.feature_passed == 1
    assert association.contingency.no_feature_failed == 1
    assert association.contingency.no_feature_passed == 3
    assert association.contingency.feature_failure_rate == Fraction(3, 4)
    assert association.contingency.failure_rate_difference == Fraction(1, 2)


def test_candidate_emitted_with_evidence_and_unverified_hypothesis(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    result = _recursion_scenario(make_submission)
    candidate = result.candidate_for(FeatureId.DIRECT_SELF_RECURSION)
    assert candidate is not None
    assert candidate.hypothesis == ()
    assert {source.location for source in candidate.evidence} == {"1", "2", "3"}
    assert len(candidate.observed) == 2
    assert candidate.limits


def test_no_candidate_when_samples_insufficient(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    result = analyze(
        "batch-1", "problem-1", [make_submission("1", _RECURSIVE, Verdict.WRONG_ANSWER)]
    )
    assert result.candidates == ()
    assert all(a.state is not AssociationState.COMPUTABLE for a in result.associations)


def test_matching_submissions_by_feature(make_submission: Callable[..., SubmissionRecord]) -> None:
    result = _recursion_scenario(make_submission)
    recursive = result.matching_submissions(FeatureId.DIRECT_SELF_RECURSION)
    non_recursive = result.matching_submissions(FeatureId.DIRECT_SELF_RECURSION, present=False)
    assert {ref.submission_id for ref in recursive} == {"1", "2", "3", "4"}
    assert {ref.submission_id for ref in non_recursive} == {"5", "6", "7", "8"}


def test_nested_loop_threshold_is_two(make_submission: Callable[..., SubmissionRecord]) -> None:
    records = [
        make_submission("1", _NESTED, Verdict.ACCEPTED),
        make_submission("2", _LOOP, Verdict.ACCEPTED),
    ]
    result = analyze("batch-1", "problem-1", records)
    nested = result.matching_submissions(FeatureId.MAX_LOOP_NESTING)
    assert {ref.submission_id for ref in nested} == {"1"}
    any_loop = result.matching_submissions(FeatureId.MAX_LOOP_NESTING, threshold=1)
    assert {ref.submission_id for ref in any_loop} == {"1", "2"}


def test_submissions_failing_testpoint(make_submission: Callable[..., SubmissionRecord]) -> None:
    from app.analysis.code import TestPointOutcome, TestPointResult

    records = [
        make_submission(
            "1",
            "x = 1",
            Verdict.ACCEPTED,
            test_points=(TestPointResult("t1", TestPointOutcome.PASSED),),
        ),
        make_submission(
            "2",
            "x = 1",
            Verdict.WRONG_ANSWER,
            test_points=(TestPointResult("t1", TestPointOutcome.FAILED),),
        ),
        make_submission(
            "3",
            "x = 1",
            Verdict.WRONG_ANSWER,
            test_points=(TestPointResult("t1", TestPointOutcome.FAILED),),
        ),
    ]
    result = analyze("batch-1", "problem-1", records)
    failing = result.submissions_failing_testpoint("t1")
    assert {ref.submission_id for ref in failing} == {"2", "3"}


def test_failed_verdict_submissions(make_submission: Callable[..., SubmissionRecord]) -> None:
    result = _recursion_scenario(make_submission)
    assert {ref.submission_id for ref in result.failed_verdict_submissions()} == {
        "1",
        "2",
        "3",
        "5",
    }


def test_hypothesis_can_only_be_unverified() -> None:
    claim = Hypothesis("recursion may relate to a stack depth limit")
    assert claim.status is HypothesisStatus.UNVERIFIED
    assert tuple(HypothesisStatus) == (HypothesisStatus.UNVERIFIED,)


def test_extractor_registry_covers_both_languages() -> None:
    assert extractor_for(Language.PYTHON) is not None
    assert extractor_for(Language.CPP) is not None


def test_association_reports_excluded_when_feature_unknown(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    # An empty source yields unknown features, so it must be excluded, not imputed.
    records = [
        make_submission("1", "", Verdict.WRONG_ANSWER),
        make_submission("2", _LOOP, Verdict.WRONG_ANSWER),
        make_submission("3", _LOOP, Verdict.ACCEPTED),
        make_submission("4", _LOOP, Verdict.ACCEPTED),
    ]
    result = analyze("batch-1", "problem-1", records)
    association = result.association_for(FeatureId.LOOPS)
    assert association is not None
    assert association.excluded_count == 1
    assert association.contingency.feature_total == 3
