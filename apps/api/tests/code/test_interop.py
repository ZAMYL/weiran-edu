from collections.abc import Callable

import pytest

from app.analysis.code import (
    CodeState,
    FeatureId,
    Language,
    SubmissionRecord,
    TestPointOutcome,
    TestPointResult,
    Verdict,
    analyze,
    b01_code_state,
    b01_language,
    b01_test_point_outcome,
    b01_verdict,
    c01_feature_associations,
    c01_feature_code,
    c01_status,
)

_RECURSIVE = "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\n"
_LOOP = "for i in range(10):\n    print(i)\n"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("答案正确", Verdict.ACCEPTED),
        ("答案错误", Verdict.WRONG_ANSWER),
        ("运行超时", Verdict.TIME_LIMIT_EXCEEDED),
        ("内存超限", Verdict.MEMORY_LIMIT_EXCEEDED),
        ("运行错误", Verdict.RUNTIME_ERROR),
        ("格式错误", Verdict.FORMAT_ERROR),
        ("编译错误", Verdict.COMPILE_ERROR),
        ("部分正确", Verdict.PARTIAL),
        ("未作答", Verdict.NOT_ATTEMPTED),
        (None, Verdict.UNKNOWN),
        ("未知状态", Verdict.UNKNOWN),
    ],
)
def test_b01_verdict_mapping(status: str | None, expected: Verdict) -> None:
    assert b01_verdict(status) is expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("答案正确", TestPointOutcome.PASSED),
        ("答案错误", TestPointOutcome.FAILED),
        ("运行超时", TestPointOutcome.FAILED),
        ("内存超限", TestPointOutcome.FAILED),
        ("运行错误", TestPointOutcome.FAILED),
        ("格式错误", TestPointOutcome.FAILED),
        ("编译错误", TestPointOutcome.UNKNOWN),
        ("部分正确", TestPointOutcome.UNKNOWN),
        ("未作答", TestPointOutcome.UNKNOWN),
        (None, TestPointOutcome.UNKNOWN),
    ],
)
def test_b01_test_point_outcome_mapping(status: str | None, expected: TestPointOutcome) -> None:
    assert b01_test_point_outcome(status) is expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("GXX", Language.CPP),
        ("G++", Language.CPP),
        ("C++", Language.CPP),
        ("Python3", Language.PYTHON),
        ("python", Language.PYTHON),
        ("GCC", None),  # ambiguous C vs C++
        ("", None),
        (None, None),
    ],
)
def test_b01_language_mapping(label: str | None, expected: Language | None) -> None:
    assert b01_language(label) is expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("PRESENT", CodeState.PRESENT),
        ("UNANSWERED", CodeState.UNANSWERED),
        ("MISSING", CodeState.MISSING),
        (None, CodeState.MISSING),
        ("UNKNOWN", CodeState.MISSING),
    ],
)
def test_b01_code_state_mapping(state: str | None, expected: CodeState) -> None:
    assert b01_code_state(state) is expected


def test_c01_status_mapping() -> None:
    assert c01_status(Verdict.ACCEPTED) == "accepted"
    assert c01_status(Verdict.WRONG_ANSWER) == "wrong_answer"
    assert c01_status(Verdict.TIME_LIMIT_EXCEEDED) == "timeout"
    assert c01_status(Verdict.MEMORY_LIMIT_EXCEEDED) == "memory_limit"
    assert c01_status(Verdict.RUNTIME_ERROR) == "runtime_error"
    assert c01_status(Verdict.FORMAT_ERROR) == "format_error"
    assert c01_status(Verdict.COMPILE_ERROR) == "compile_error"
    assert c01_status(Verdict.PARTIAL) == "partial"
    assert c01_status(Verdict.NOT_ATTEMPTED) == "not_attempted"


def test_c01_feature_code_mapping() -> None:
    assert c01_feature_code(FeatureId.BRANCHES) == "boundary_check"
    assert c01_feature_code(FeatureId.LOOPS) == "loop"
    assert c01_feature_code(FeatureId.DIRECT_SELF_RECURSION) == "recursion"
    assert c01_feature_code(FeatureId.INDEX_ACCESSES) == "indexing"
    assert c01_feature_code(FeatureId.IO_OPERATIONS) == "input_parsing"
    assert c01_feature_code(FeatureId.MAX_LOOP_NESTING) is None
    assert c01_feature_code(FeatureId.FUNCTIONS) is None
    assert c01_feature_code(FeatureId.CONTAINER_USES) is None


def _scenario(make_submission: Callable[..., SubmissionRecord]):
    records = [
        make_submission("1", _RECURSIVE, Verdict.WRONG_ANSWER),
        make_submission("2", _RECURSIVE, Verdict.WRONG_ANSWER),
        make_submission("3", _LOOP, Verdict.WRONG_ANSWER),
        make_submission("4", _LOOP, Verdict.WRONG_ANSWER),
        make_submission("5", _RECURSIVE, Verdict.ACCEPTED),
        make_submission("6", _LOOP, Verdict.ACCEPTED),
    ]
    return analyze("batch-1", "problem-1", records)


def test_c01_feature_associations_shape(make_submission: Callable[..., SubmissionRecord]) -> None:
    result = _scenario(make_submission)
    associations = c01_feature_associations(result, status="wrong_answer")
    by_code = {association.code: association for association in associations}

    assert set(by_code) == {"boundary_check", "loop", "indexing", "recursion", "input_parsing"}

    recursion = by_code["recursion"]
    assert recursion.problem == "problem-1"
    assert recursion.status == "wrong_answer"
    assert set(recursion.checked) == {"student-1", "student-2", "student-3", "student-4"}
    assert set(recursion.present) == {("student-1", "1"), ("student-2", "2")}

    loop = by_code["loop"]
    assert set(loop.present) == {("student-3", "3"), ("student-4", "4")}

    indexing = by_code["indexing"]
    assert indexing.present == ()


def test_c01_feature_associations_point_filter(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    records = [
        make_submission(
            "1",
            _RECURSIVE,
            Verdict.WRONG_ANSWER,
            test_points=(TestPointResult("t1", TestPointOutcome.FAILED),),
        ),
        make_submission(
            "2",
            _RECURSIVE,
            Verdict.WRONG_ANSWER,
            test_points=(TestPointResult("t1", TestPointOutcome.PASSED),),
        ),
        make_submission(
            "3",
            _LOOP,
            Verdict.ACCEPTED,
            test_points=(TestPointResult("t1", TestPointOutcome.PASSED),),
        ),
    ]
    result = analyze("batch-1", "problem-1", records)
    associations = c01_feature_associations(result, status="wrong_answer", point="t1")
    recursion = next(a for a in associations if a.code == "recursion")
    assert recursion.point == "t1"
    assert recursion.checked == ("student-1",)
    assert recursion.present == (("student-1", "1"),)
