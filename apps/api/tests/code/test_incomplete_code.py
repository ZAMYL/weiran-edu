"""Incomplete or untrusted code never yields features presented as complete code facts."""

from collections.abc import Callable

from app.analysis.code import (
    CodeState,
    FeatureId,
    ParseStatus,
    SubmissionRecord,
    Verdict,
    analyze,
    c01_feature_associations,
)


def test_unanswered_code_is_unknown(make_submission: Callable[..., SubmissionRecord]) -> None:
    record = make_submission("1", "", Verdict.WRONG_ANSWER, code_state=CodeState.UNANSWERED)
    features = analyze("batch-1", "problem-1", [record]).submissions[0].features
    assert features.status is ParseStatus.MISSING_SOURCE
    assert features.detail == "code_state_unanswered"
    assert all(value.value is None for value in features.values)


def test_missing_code_is_unknown(make_submission: Callable[..., SubmissionRecord]) -> None:
    record = make_submission("1", "", Verdict.WRONG_ANSWER, code_state=CodeState.MISSING)
    features = analyze("batch-1", "problem-1", [record]).submissions[0].features
    assert features.status is ParseStatus.MISSING_SOURCE
    assert features.detail == "code_state_missing"
    assert all(value.value is None for value in features.values)


def test_truncated_code_is_unknown(make_submission: Callable[..., SubmissionRecord]) -> None:
    record = make_submission("1", "for i in range(10):", Verdict.WRONG_ANSWER, code_truncated=True)
    features = analyze("batch-1", "problem-1", [record]).submissions[0].features
    assert features.status is ParseStatus.TRUNCATED
    assert features.detail == "code_truncated"
    assert all(value.value is None for value in features.values)


def test_undetermined_language_is_unknown(make_submission: Callable[..., SubmissionRecord]) -> None:
    record = make_submission("1", "print(1)", Verdict.WRONG_ANSWER, language=None)
    features = analyze("batch-1", "problem-1", [record]).submissions[0].features
    assert features.status is ParseStatus.UNKNOWN_LANGUAGE
    assert all(value.value is None for value in features.values)


def test_source_statuses_preserve_exclusion_reasons(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    records = [
        make_submission("1", "print(1)", Verdict.ACCEPTED),
        make_submission("2", "", Verdict.WRONG_ANSWER, code_state=CodeState.MISSING),
        make_submission("3", "for i in range(3):", Verdict.WRONG_ANSWER, code_truncated=True),
    ]
    result = analyze("batch-1", "problem-1", records)
    by_status = {item.status: item.count for item in result.source_statuses}
    assert by_status[ParseStatus.OK] == 1
    assert by_status[ParseStatus.MISSING_SOURCE] == 1
    assert by_status[ParseStatus.TRUNCATED] == 1


def test_incomplete_code_is_excluded_from_association(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    records = [
        make_submission("1", "print(1)", Verdict.WRONG_ANSWER),
        make_submission("2", "print(1)", Verdict.ACCEPTED),
        make_submission("3", "", Verdict.WRONG_ANSWER, code_state=CodeState.MISSING),
        make_submission("4", "for i in range(3):", Verdict.WRONG_ANSWER, code_truncated=True),
    ]
    result = analyze("batch-1", "problem-1", records)
    association = result.association_for(FeatureId.IO_OPERATIONS)
    assert association is not None
    assert association.excluded_count == 2


def test_c01_association_excludes_incomplete_code(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    loop_code = "for i in range(3):\n    print(i)\n"
    records = [
        make_submission("1", loop_code, Verdict.WRONG_ANSWER),
        make_submission("2", loop_code, Verdict.WRONG_ANSWER, code_truncated=True),
    ]
    result = analyze("batch-1", "problem-1", records)
    loop = next(
        a for a in c01_feature_associations(result, status="wrong_answer") if a.code == "loop"
    )
    assert loop.checked == ("student-1",)
    assert loop.present == (("student-1", "1"),)
