from collections.abc import Callable
from fractions import Fraction
from statistics import correlation

import pytest

from app.analysis.edu import (
    AnalysisInputError,
    AssessmentRecord,
    Condition,
    CorrelationState,
    Dimension,
    Operator,
    ScorePolicy,
    prepare_dataset,
    select_students,
    spearman_correlations,
    student_profile,
    summarize,
)
from app.analysis.edu.models import Dataset, RawScore

A, IMPLEMENTATION = Dimension.ALGORITHMS, Dimension.IMPLEMENTATION


def pair_rows(
    make_record: Callable[..., AssessmentRecord], x: tuple[RawScore, ...], y: tuple[RawScore, ...]
) -> Dataset:
    return prepare_dataset(
        [
            make_record(f"synthetic-{i}", overrides={A: left, IMPLEMENTATION: right})
            for i, (left, right) in enumerate(zip(x, y, strict=True))
        ],
        policy=ScorePolicy(),
    )


def test_tied_spearman_has_hand_computed_average_ranks(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    data = pair_rows(make_record, (1, 1, 2, 3), (1, 2, 2, 3))
    result = spearman_correlations(data, batch_id="synthetic-batch")
    pair = result.pairs[0]
    # Ranks [1.5,1.5,3,4] and [1,2.5,2.5,4]:
    # centered dot product 3.75 / sqrt(4.5*4.5) = 5/6.
    assert pair.state is CorrelationState.COMPUTABLE
    assert pair.coefficient == pytest.approx(5 / 6, abs=1e-12)
    assert pair.paired_count == 4
    assert [point.left_rank for point in pair.observations] == [1.5, 1.5, 3, 4]
    assert [point.right_rank for point in pair.observations] == [1, 2.5, 2.5, 4]
    assert tuple(point.reference for point in pair.observations) == result.scope.records
    assert len(result.pairs) == 10


@pytest.mark.parametrize(
    "x,y,expected",
    [
        ((1, 2, 3), (1, 2, 3), 1),
        ((1, 2, 3), (3, 2, 1), -1),
        ((1, 2, 3), (1, 3, 2), 0.5),
        ((1, 2, 3, 4), (2, 4, 1, 3), 0),
    ],
)
def test_known_correlations_and_independent_standard_library_oracle(
    make_record: Callable[..., AssessmentRecord],
    x: tuple[int, ...],
    y: tuple[int, ...],
    expected: float,
) -> None:
    pair = spearman_correlations(pair_rows(make_record, x, y), batch_id="synthetic-batch").pairs[0]
    assert pair.coefficient == pytest.approx(expected, abs=1e-12)
    assert pair.coefficient == pytest.approx(correlation(x, y, method="ranked"), abs=1e-12)


def test_pairwise_complete_samples_are_ranked_again_within_each_pair(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    data = pair_rows(make_record, (1, 2, 3, 4, 5), (1, None, 2, 3, "invalid"))
    result = spearman_correlations(data, batch_id="synthetic-batch")
    pair = result.pairs[0]
    assert pair.paired_count == 3
    assert pair.coefficient == pytest.approx(1)
    assert [point.left_rank for point in pair.observations] == [1, 2, 3]
    assert [point.left_score for point in pair.observations] == [1, 3, 4]
    assert [ref.student_id for ref in pair.excluded_records] == ["synthetic-1", "synthetic-4"]
    assert set(point.reference for point in pair.observations) | set(pair.excluded_records) == set(
        result.scope.records
    )


@pytest.mark.parametrize("size", [0, 1])
def test_insufficient_samples_are_uncomputable(
    make_record: Callable[..., AssessmentRecord],
    size: int,
) -> None:
    data = pair_rows(make_record, (1,) * size, (2,) * size)
    assert all(
        pair.state is CorrelationState.INSUFFICIENT_PAIRS and pair.coefficient is None
        for pair in spearman_correlations(data, batch_id="synthetic-batch").pairs
    )


def test_constant_dimension_and_configured_minimum_pairs(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    data = pair_rows(make_record, (1, 1, 1), (1, 2, 3))
    pair = spearman_correlations(data, batch_id="synthetic-batch").pairs[0]
    assert pair.state is CorrelationState.CONSTANT_INPUT and pair.coefficient is None
    stricter = spearman_correlations(data, batch_id="synthetic-batch", minimum_pairs=4).pairs[0]
    assert stricter.state is CorrelationState.INSUFFICIENT_PAIRS
    assert stricter.paired_count == 3


@pytest.mark.parametrize("minimum", [1, True, 2.5])
def test_invalid_minimum_pairs_is_rejected(dataset: Dataset, minimum: object) -> None:
    with pytest.raises(AnalysisInputError):
        spearman_correlations(dataset, batch_id="synthetic-batch", minimum_pairs=minimum)


def test_scope_and_input_order_are_reproducible_for_all_four_analyses(
    records: tuple[AssessmentRecord, ...],
) -> None:
    first = prepare_dataset(records, policy=ScorePolicy())
    second = prepare_dataset(reversed(records), policy=ScorePolicy())
    condition = Condition(A, Operator.LE, Fraction(3))
    assert first == second
    assert summarize(first, batch_id="synthetic-batch") == summarize(
        second, batch_id="synthetic-batch"
    )
    assert select_students(first, batch_id="synthetic-batch", conditions=[condition]) == (
        select_students(second, batch_id="synthetic-batch", conditions=[condition])
    )
    assert student_profile(first, batch_id="synthetic-batch", student_id="synthetic-001") == (
        student_profile(second, batch_id="synthetic-batch", student_id="synthetic-001")
    )
    assert spearman_correlations(first, batch_id="synthetic-batch") == (
        spearman_correlations(second, batch_id="synthetic-batch")
    )


def test_subset_and_other_batch_never_enter_correlations(
    records: tuple[AssessmentRecord, ...],
    make_record: Callable[..., AssessmentRecord],
) -> None:
    data = prepare_dataset(
        [*records, make_record("synthetic-001", batch="other-batch", overrides={A: 5})],
        policy=ScorePolicy(),
    )
    result = spearman_correlations(
        data, batch_id="synthetic-batch", student_ids=["synthetic-001", "synthetic-002"]
    )
    assert all(pair.paired_count == 2 for pair in result.pairs)
    assert all(
        point.reference.batch_id == "synthetic-batch"
        for pair in result.pairs
        for point in pair.observations
    )


def test_seeded_tie_patterns_match_stdlib_ranked_correlation(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    from random import Random

    random = Random(20261023)
    for size in range(2, 18):
        x = tuple(random.randint(1, 5) for _ in range(size))
        y = tuple(random.randint(1, 5) for _ in range(size))
        pair = spearman_correlations(
            pair_rows(make_record, x, y), batch_id="synthetic-batch"
        ).pairs[0]
        if len(set(x)) == 1 or len(set(y)) == 1:
            assert pair.state is CorrelationState.CONSTANT_INPUT
        else:
            assert pair.coefficient == pytest.approx(correlation(x, y, method="ranked"), abs=1e-12)
