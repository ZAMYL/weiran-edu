from collections.abc import Callable

import pytest

from app.analysis.code import (
    CodeAnalysisError,
    ParseStatus,
    SubmissionRecord,
    TestPointOutcome,
    TestPointResult,
    Verdict,
    analyze,
)


def _tp(testpoint_id: str, outcome: TestPointOutcome) -> TestPointResult:
    return TestPointResult(testpoint_id, outcome)


def test_outcome_partitioning(make_submission: Callable[..., SubmissionRecord]) -> None:
    records = [
        make_submission("1", "x = 1", Verdict.ACCEPTED),
        make_submission("2", "x = 1", Verdict.WRONG_ANSWER),
        make_submission("3", "x = 1", Verdict.TIME_LIMIT_EXCEEDED),
        make_submission("4", "x = 1", Verdict.COMPILE_ERROR),
        make_submission("5", "x = 1", Verdict.UNKNOWN),
    ]
    result = analyze("batch-1", "problem-1", records)
    assert (result.outcomes.passed, result.outcomes.failed, result.outcomes.other) == (1, 2, 2)
    assert result.outcomes.judged == 3


def test_duplicate_submission_rejected(make_submission: Callable[..., SubmissionRecord]) -> None:
    records = [
        make_submission("1", "x = 1"),
        make_submission("1", "x = 2"),
    ]
    with pytest.raises(CodeAnalysisError) as exc:
        analyze("batch-1", "problem-1", records)
    assert exc.value.code == "duplicate_submission"


def test_inconsistent_identity_rejected(make_submission: Callable[..., SubmissionRecord]) -> None:
    records = [
        make_submission("1", "x = 1", student_id="alice"),
        make_submission("1", "x = 2", student_id="bob"),
    ]
    with pytest.raises(CodeAnalysisError) as exc:
        analyze("batch-1", "problem-1", records)
    assert exc.value.code == "inconsistent_submission_identity"


def test_out_of_scope_rejected(make_submission: Callable[..., SubmissionRecord]) -> None:
    with pytest.raises(CodeAnalysisError) as exc:
        analyze("batch-1", "problem-1", [make_submission("1", "x = 1", problem_id="other")])
    assert exc.value.code == "submission_outside_scope"


def test_duplicate_testpoint_rejected(make_submission: Callable[..., SubmissionRecord]) -> None:
    record = make_submission(
        "1",
        "x = 1",
        test_points=(_tp("t1", TestPointOutcome.PASSED), _tp("t1", TestPointOutcome.FAILED)),
    )
    with pytest.raises(CodeAnalysisError) as exc:
        analyze("batch-1", "problem-1", [record])
    assert exc.value.code == "duplicate_testpoint_in_submission"


def test_testpoint_aggregation(make_submission: Callable[..., SubmissionRecord]) -> None:
    records = [
        make_submission(
            "1",
            "x = 1",
            Verdict.ACCEPTED,
            test_points=(_tp("t1", TestPointOutcome.PASSED), _tp("t2", TestPointOutcome.PASSED)),
        ),
        make_submission(
            "2",
            "x = 1",
            Verdict.WRONG_ANSWER,
            test_points=(_tp("t1", TestPointOutcome.FAILED), _tp("t2", TestPointOutcome.UNKNOWN)),
        ),
    ]
    result = analyze("batch-1", "problem-1", records)
    t1, t2 = result.test_points
    assert (t1.testpoint_id, t1.passed, t1.failed, t1.unknown) == ("t1", 1, 1, 0)
    assert (t2.testpoint_id, t2.passed, t2.failed, t2.unknown) == ("t2", 1, 0, 1)


def test_missing_source_is_unknown_not_imputed(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    records = [
        make_submission("1", "", Verdict.WRONG_ANSWER),
        make_submission("2", "print(1)", Verdict.ACCEPTED),
    ]
    result = analyze("batch-1", "problem-1", records)
    missing = result.submissions[0]
    assert missing.features.status is ParseStatus.MISSING_SOURCE
    assert all(value.value is None for value in missing.features.values)


def test_output_is_sorted_by_submission_id(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    records = [
        make_submission("c", "x = 1"),
        make_submission("a", "x = 1"),
        make_submission("b", "x = 1"),
    ]
    result = analyze("batch-1", "problem-1", records)
    assert [s.reference.submission_id for s in result.submissions] == ["a", "b", "c"]
