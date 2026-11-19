from collections.abc import Callable

import pytest

from app.analysis.code import (
    Analysis,
    CodeAnalysisError,
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
    b01_test_point_result,
    b01_verdict,
    c01_feature_associations,
    c01_feature_code,
    c01_status,
    c01_test_point_status,
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
        ("运行时错误", Verdict.RUNTIME_ERROR),
        ("段错误", Verdict.RUNTIME_ERROR),
        ("非零返回", Verdict.RUNTIME_ERROR),
        ("格式错误", Verdict.FORMAT_ERROR),
        ("编译错误", Verdict.COMPILE_ERROR),
        ("部分正确", Verdict.PARTIAL),
        ("未作答", Verdict.NOT_ATTEMPTED),
        (None, Verdict.UNKNOWN),
        ("未知状态", Verdict.UNKNOWN),
        ("评测中", Verdict.UNKNOWN),
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
        ("运行时错误", TestPointOutcome.FAILED),
        ("段错误", TestPointOutcome.FAILED),
        ("非零返回", TestPointOutcome.FAILED),
        ("格式错误", TestPointOutcome.FAILED),
        ("编译错误", TestPointOutcome.UNKNOWN),
        ("部分正确", TestPointOutcome.UNKNOWN),
        ("未作答", TestPointOutcome.UNKNOWN),
        (None, TestPointOutcome.UNKNOWN),
        ("评测中", TestPointOutcome.UNKNOWN),
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


@pytest.fixture
def point_analysis(
    make_submission: Callable[..., SubmissionRecord],
) -> Analysis:
    wrong = b01_test_point_result("t1", "答案错误")
    timeout = b01_test_point_result("t1", "运行超时")
    records = [
        make_submission(
            "1",
            _RECURSIVE,
            Verdict.PARTIAL,
            test_points=(wrong, b01_test_point_result("t2", "运行超时")),
        ),
        make_submission(
            "2",
            _LOOP,
            Verdict.WRONG_ANSWER,
            test_points=(timeout, b01_test_point_result("t2", "答案错误")),
        ),
        make_submission(
            "3",
            _RECURSIVE,
            Verdict.WRONG_ANSWER,
            test_points=(b01_test_point_result("t1", "答案正确"),),
        ),
        make_submission(
            "4",
            _RECURSIVE,
            Verdict.WRONG_ANSWER,
            test_points=(b01_test_point_result("t1", "评测中"),),
        ),
        make_submission(
            "5",
            _RECURSIVE,
            Verdict.WRONG_ANSWER,
            test_points=(TestPointResult("t1", TestPointOutcome.FAILED),),
        ),
        make_submission("6", _RECURSIVE, Verdict.WRONG_ANSWER),
        make_submission(
            "7", "", Verdict.WRONG_ANSWER, test_points=(wrong,), code_state=CodeState.MISSING
        ),
        make_submission(
            "8", _RECURSIVE, Verdict.WRONG_ANSWER, test_points=(wrong,), code_truncated=True
        ),
        make_submission("9", _RECURSIVE, Verdict.WRONG_ANSWER, test_points=(wrong,), language=None),
    ]
    return analyze("batch-1", "problem-1", records)


@pytest.mark.parametrize(
    ("point", "status", "checked", "present"),
    [
        ("t1", "wrong_answer", ("student-1",), (("student-1", "1"),)),
        ("t1", "timeout", ("student-2",), ()),
        ("t2", "wrong_answer", ("student-2",), ()),
        ("t2", "timeout", ("student-1",), (("student-1", "1"),)),
        ("t1", "accepted", (), ()),
        ("t1", "unknown", (), ()),
        ("uncovered", "wrong_answer", (), ()),
    ],
)
def test_c01_feature_associations_point_filter(
    point_analysis: Analysis,
    point: str,
    status: str,
    checked: tuple[str, ...],
    present: tuple[tuple[str, str], ...],
) -> None:
    associations = c01_feature_associations(point_analysis, status=status, point=point)
    recursion = next(a for a in associations if a.code == "recursion")
    assert (recursion.point, recursion.status) == (point, status)
    assert recursion.checked == checked
    assert recursion.present == present
    assert all(person in a.checked for a in associations for person, _ in a.present)


def test_whole_submission_cohort_and_coarse_counts_are_preserved(point_analysis: Analysis) -> None:
    associations = c01_feature_associations(point_analysis, status="wrong_answer")
    recursion = next(a for a in associations if a.code == "recursion")
    assert recursion.checked == tuple(f"student-{i}" for i in range(2, 7))
    assert point_analysis.outcomes.failed == 8
    coarse = next(point for point in point_analysis.test_points if point.testpoint_id == "t1")
    assert (coarse.passed, coarse.failed, coarse.unknown) == (1, 6, 1)


@pytest.mark.parametrize(
    "point",
    [
        TestPointResult("t1", TestPointOutcome.PASSED, Verdict.WRONG_ANSWER),
        TestPointResult("t1", TestPointOutcome.UNKNOWN, Verdict.WRONG_ANSWER),
        TestPointResult("t1", TestPointOutcome.FAILED, Verdict.COMPILE_ERROR),
    ],
)
def test_invalid_specific_point_failure_is_rejected(
    make_submission: Callable[..., SubmissionRecord], point: TestPointResult
) -> None:
    record = make_submission("1", _LOOP, test_points=(point,))
    with pytest.raises(CodeAnalysisError, match="invalid_testpoint_failure_verdict"):
        analyze("batch-1", "problem-1", (record,))


# Mirrors ``importing/edu/answersheet.py`` ``_STATUS_WORDS`` at B-01 ``a0d1422``.
_B01_CANONICAL_WORDS = (
    "答案正确",
    "答案错误",
    "运行时错误",
    "运行超时",
    "运行错误",
    "内存超限",
    "段错误",
    "非零返回",
    "编译错误",
    "格式错误",
    "部分正确",
    "未作答",
)

# Mirrors C-01's ``Verdict`` union in ``prototypes/research_agent_v2/programming_evidence.py``.
_C01_VERDICTS = frozenset(
    {
        "accepted",
        "wrong_answer",
        "timeout",
        "memory_limit",
        "runtime_error",
        "format_error",
        "other_failure",
        "unknown",
        "not_attempted",
        "compile_error",
        "partial",
    }
)


def test_b01_canonical_words_have_consistent_meaning_across_contracts() -> None:
    """Every canonical B-01 word maps to a typed verdict whose C-01 status exists."""
    for word in _B01_CANONICAL_WORDS:
        verdict = b01_verdict(word)
        assert verdict is not Verdict.UNKNOWN, word
        assert c01_status(verdict) in _C01_VERDICTS, word


@pytest.mark.parametrize("word", ["运行错误", "运行时错误", "段错误", "非零返回"])
def test_runtime_family_means_runtime_error_everywhere(word: str) -> None:
    """The runtime family shares one meaning in B-01, DS-03 and C-01 (PR #112 parity)."""
    assert b01_verdict(word) is Verdict.RUNTIME_ERROR
    assert c01_status(b01_verdict(word)) == "runtime_error"
    assert b01_test_point_outcome(word) is TestPointOutcome.FAILED
    assert c01_test_point_status(b01_test_point_result("t1", word)) == "runtime_error"


def test_unrecognized_status_never_enters_statistical_denominators(
    make_submission: Callable[..., SubmissionRecord],
) -> None:
    """Unknown verdict/test statuses stay unknown and out of pass/fail counts."""
    records = [
        make_submission("1", _RECURSIVE, b01_verdict("答案错误")),
        make_submission("2", _LOOP, b01_verdict("答案正确")),
        make_submission(
            "3",
            _LOOP,
            b01_verdict("评测中"),
            test_points=(TestPointResult("t1", b01_test_point_outcome("评测中")),),
        ),
    ]
    result = analyze("batch-1", "problem-1", records)

    assert b01_verdict("评测中") is Verdict.UNKNOWN
    assert c01_status(b01_verdict("评测中")) == "unknown"
    assert result.outcomes.passed == 1
    assert result.outcomes.failed == 1
    assert result.outcomes.other == 1
    for association in result.associations:
        contingency = association.contingency
        # Only the two judged submissions may enter any 2x2 cell.
        assert contingency.feature_total + contingency.no_feature_total == 2
    aggregate = next(point for point in result.test_points if point.testpoint_id == "t1")
    assert (aggregate.passed, aggregate.failed, aggregate.unknown) == (0, 0, 1)
