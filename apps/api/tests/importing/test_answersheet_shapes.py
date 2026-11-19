"""不同人数与结构的合成样本：验证解析器不依赖特定人数、题号或题量。"""

from app.importing.edu.answersheet import (
    CATEGORY_BLANK_PAPER,
    CATEGORY_STANDARD_ANSWER,
    CATEGORY_STUDENT,
    CATEGORY_TEST_DATA,
    parse_answersheet,
)


def test_five_students_four_problems(make_answersheet):
    package = make_answersheet(
        students=5, problems=["A", "B", "C", "D"], observations_per_problem=3
    )

    evidence = parse_answersheet(package)

    assert len(evidence.students) == 5
    assert [problem.label for problem in evidence.problems] == ["A", "B", "C", "D"]
    assert len(evidence.submissions) == 5 * 4
    assert len(evidence.observations) == 5 * 4 * 3


def test_single_student_with_eight_problems(make_answersheet):
    package = make_answersheet(
        students=1,
        problems=[f"P{index}" for index in range(1, 9)],
        observations_per_problem=1,
    )

    evidence = parse_answersheet(package)

    assert len(evidence.students) == 1
    assert len(evidence.problems) == 8
    assert len(evidence.submissions) == 8
    assert len(evidence.observations) == 8


def test_twelve_problems_keep_their_order(make_answersheet):
    labels = [f"7-{index}" for index in range(1, 13)]
    package = make_answersheet(students=2, problems=labels, observations_per_problem=2)

    evidence = parse_answersheet(package)

    assert [problem.label for problem in evidence.problems] == labels
    assert [problem.display_ordinal for problem in evidence.problems] == list(range(12))
    # 所有文件题序一致，可以如实声称是原始顺序
    assert evidence.order_basis == "SOURCE"


def test_non_student_files_never_reach_the_counts(make_answersheet):
    package = make_answersheet(
        students=3,
        problems=["A", "B"],
        include_reference=True,
        include_verify=True,
    )

    evidence = parse_answersheet(package)

    assert len(evidence.students) == 3
    assert len(evidence.submissions) == 6
    categories = {material.category for material in evidence.materials}
    assert categories == {
        CATEGORY_STUDENT,
        CATEGORY_STANDARD_ANSWER,
        CATEGORY_BLANK_PAPER,
        CATEGORY_TEST_DATA,
    }


def test_compiler_output_travels_with_each_submission(make_answersheet):
    package = make_answersheet(students=2, problems=["A", "B"], compiler="warning: unused")

    evidence = parse_answersheet(package)

    assert all(
        submission.compiler_output == "warning: unused" for submission in evidence.submissions
    )
