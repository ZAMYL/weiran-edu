from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction

import pytest

from app.analysis.edu import (
    AnalysisInputError,
    AssessmentRecord,
    Condition,
    Dimension,
    Operator,
    ScorePolicy,
    ScoreState,
    prepare_dataset,
    select_students,
    student_profile,
)
from app.analysis.edu.models import Dataset

A, E, S = Dimension.ALGORITHMS, Dimension.EFFICIENCY, Dimension.STRATEGY


def test_and_selection_matches_hand_count_and_exact_sources(dataset: Dataset) -> None:
    conditions = (
        Condition(S, Operator.GE, Fraction(4)),
        Condition(A, Operator.LT, Fraction(3)),
        Condition(E, Operator.LE, Fraction(2)),
    )
    result = select_students(dataset, batch_id="synthetic-batch", conditions=conditions)
    # First two of four records satisfy all three conditions: 2/4 = 1/2.
    assert result.student_ids == ("synthetic-001", "synthetic-002")
    assert result.conditions == conditions
    assert (result.matched_share.count, result.matched_share.denominator) == (2, 4)
    assert result.matched_share.proportion == Fraction(1, 2)
    assert result.eligible_count == 4
    assert result.matches == tuple(record.reference for record in dataset.records[:2])
    assert result.matches[1].evidence_refs == dataset.records[1].reference.evidence_refs


@pytest.mark.parametrize(
    "operator,expected",
    [(Operator.LT, 2), (Operator.LE, 3), (Operator.EQ, 1), (Operator.GE, 2), (Operator.GT, 1)],
)
def test_threshold_operators_are_strictly_distinct(
    dataset: Dataset, operator: Operator, expected: int
) -> None:
    result = select_students(
        dataset, batch_id="synthetic-batch", conditions=[Condition(A, operator, Fraction(3))]
    )
    assert result.matched_share.count == expected


def test_missing_queried_dimension_is_unknown_not_a_zero_or_a_negative(
    records: tuple[AssessmentRecord, ...],
    make_record: Callable[..., AssessmentRecord],
) -> None:
    extra = make_record("synthetic-005", overrides={A: None, S: 4})
    data = prepare_dataset([*records, extra], policy=ScorePolicy())
    result = select_students(
        data, batch_id="synthetic-batch", conditions=[Condition(A, Operator.LT, Fraction(3))]
    )
    assert result.eligible_count == 4
    assert result.matched_share.proportion == Fraction(2, 5)
    assert result.matched_among_eligible.proportion == Fraction(1, 2)
    assert result.unevaluable_records == (data.records[-1].reference,)
    valid_query = select_students(
        data, batch_id="synthetic-batch", conditions=[Condition(S, Operator.GE, Fraction(4))]
    )
    assert valid_query.eligible_count == 5  # Unqueried missing A does not exclude a student.


def test_invalid_queried_score_and_zero_denominators(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    condition = Condition(A, Operator.LT, Fraction(3))
    invalid = prepare_dataset(
        [make_record("synthetic-a", overrides={A: "invalid"})], policy=ScorePolicy()
    )
    result = select_students(invalid, batch_id="synthetic-batch", conditions=[condition])
    assert result.matched_share.proportion == 0
    assert result.matched_among_eligible.proportion is None
    empty = select_students(
        prepare_dataset([], policy=ScorePolicy()), batch_id="empty", conditions=[condition]
    )
    assert empty.matched_share.proportion is None
    assert empty.student_ids == ()


def test_overlapping_groups_cannot_be_added_as_unique_counts(dataset: Dataset) -> None:
    low = select_students(
        dataset, batch_id="synthetic-batch", conditions=[Condition(A, Operator.LE, Fraction(3))]
    )
    strategic = select_students(
        dataset, batch_id="synthetic-batch", conditions=[Condition(S, Operator.GE, Fraction(4))]
    )
    assert len(set(low.student_ids) & set(strategic.student_ids)) == 2
    assert len(set(low.student_ids) | set(strategic.student_ids)) == 4
    assert low.matched_share.count + strategic.matched_share.count == 6


def test_profile_retains_raw_score_comparison_and_dimension_evidence(dataset: Dataset) -> None:
    profile = student_profile(dataset, batch_id="synthetic-batch", student_id="synthetic-002")
    algorithm = profile.dimensions[0]
    # Raw 2.5 compared with 23/8 = 2.875 gives -3/8; cohort includes this student.
    assert algorithm.score.raw_value == 2.5
    assert algorithm.score.value == Fraction(5, 2)
    assert algorithm.cohort.mean == Fraction(23, 8)
    assert algorithm.difference_from_mean == Fraction(-3, 8)
    assert profile.reference == dataset.records[1].reference
    assert algorithm.evidence_refs == profile.reference.evidence_refs[:2]
    assert len(profile.dimensions) == 5
    assert profile.scope.student_count == 4


def test_profile_preserves_missing_and_invalid_states(
    records: tuple[AssessmentRecord, ...],
) -> None:
    changed = replace(records[1], scores=records[1].scores | {A: None, E: "invalid"})
    data = prepare_dataset([records[0], changed, *records[2:]], policy=ScorePolicy())
    profile = student_profile(data, batch_id="synthetic-batch", student_id="synthetic-002")
    assert profile.dimensions[0].score.state is ScoreState.MISSING
    assert profile.dimensions[0].difference_from_mean is None
    assert profile.dimensions[0].cohort.valid_count == 3
    assert profile.dimensions[3].score.state is ScoreState.INVALID
    assert profile.dimensions[3].difference_from_mean is None


def test_profile_requires_target_in_same_selected_cohort(dataset: Dataset) -> None:
    with pytest.raises(AnalysisInputError, match="student_not_in_cohort"):
        student_profile(dataset, batch_id="other-batch", student_id="synthetic-001")
    with pytest.raises(AnalysisInputError, match="student_not_in_cohort"):
        student_profile(
            dataset,
            batch_id="synthetic-batch",
            student_id="synthetic-001",
            student_ids=["synthetic-002"],
        )


@pytest.mark.parametrize("threshold", [True, "3", float("nan"), float("inf")])
def test_bad_threshold_is_rejected(threshold: object) -> None:
    with pytest.raises(AnalysisInputError):
        Condition(A, Operator.GE, threshold)


def test_query_must_be_nonempty_and_typed(dataset: Dataset) -> None:
    with pytest.raises(AnalysisInputError):
        select_students(dataset, batch_id="synthetic-batch", conditions=[])
    with pytest.raises(AnalysisInputError):
        Condition(A, "<", Fraction(3))
