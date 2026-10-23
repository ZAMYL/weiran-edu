from collections.abc import Callable
from fractions import Fraction

import pytest

from app.analysis.edu import (
    AnalysisInputError,
    AssessmentRecord,
    Dimension,
    ScorePolicy,
    prepare_dataset,
    summarize,
)
from app.analysis.edu.models import Dataset


def test_all_five_statistics_have_hand_computed_expected_values(dataset: Dataset) -> None:
    summary = summarize(dataset, batch_id="synthetic-batch")
    # Algorithm: (2 + 2.5 + 3 + 4)/4 = 23/8; middle two average = 11/4.
    means = [Fraction(23, 8), Fraction(7, 2), Fraction(27, 8), Fraction(17, 8), Fraction(33, 8)]
    medians = [Fraction(11, 4), Fraction(7, 2), Fraction(7, 2), Fraction(2), Fraction(17, 4)]
    minima = [2, 2, 2, Fraction(3, 2), 3]
    maxima = [4, 5, Fraction(9, 2), 3, 5]
    assert summary.scope.student_count == 4
    for stat, mean, middle, low, high in zip(
        summary.dimensions, means, medians, minima, maxima, strict=True
    ):
        assert (stat.valid_count, stat.missing_count, stat.invalid_count) == (4, 0, 0)
        assert (stat.mean, stat.median, stat.minimum, stat.maximum) == (mean, middle, low, high)
        assert sum(item.share.count for item in stat.distribution) == 4
        assert sum(item.share.proportion for item in stat.distribution) == 1
        assert tuple(ref.student_id for ref in stat.valid_records) == summary.scope.student_ids
    algorithm = summary.dimension(Dimension.ALGORITHMS)
    assert tuple(item.score for item in algorithm.distribution) == (2, Fraction(5, 2), 3, 4)
    assert all(
        item.share.count == 1 and item.share.denominator == 4 for item in algorithm.distribution
    )


def test_distribution_counts_ties_with_valid_only_denominator(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    rows = [
        make_record(f"synthetic-{i}", overrides={Dimension.ALGORITHMS: score})
        for i, score in enumerate((2.5, 2.5, None, "invalid", 4))
    ]
    data = prepare_dataset(rows, policy=ScorePolicy())
    stat = summarize(data, batch_id="synthetic-batch").dimension(Dimension.ALGORITHMS)
    assert (stat.valid_count, stat.missing_count, stat.invalid_count) == (3, 1, 1)
    assert stat.mean == 3  # (2.5 + 2.5 + 4)/3, never divided by 5 or zero-filled.
    assert [(bin_.score, bin_.share.proportion) for bin_ in stat.distribution] == [
        (Fraction(5, 2), Fraction(2, 3)),
        (Fraction(4), Fraction(1, 3)),
    ]


def test_empty_and_single_student(make_record: Callable[..., AssessmentRecord]) -> None:
    empty = summarize(prepare_dataset([], policy=ScorePolicy()), batch_id="empty")
    assert empty.scope.student_count == 0
    assert len(empty.dimensions) == 5
    assert all(stat.mean is None and stat.median is None for stat in empty.dimensions)
    single = summarize(
        prepare_dataset([make_record("synthetic-one")], policy=ScorePolicy()),
        batch_id="synthetic-batch",
    )
    assert all(
        (stat.mean, stat.median, stat.minimum, stat.maximum) == (1, 1, 1, 1)
        for stat in single.dimensions
    )


def test_requested_cohort_is_deduplicated_and_empty_is_not_all(dataset: Dataset) -> None:
    summary = summarize(
        dataset,
        batch_id="synthetic-batch",
        student_ids=["synthetic-002", "synthetic-001", "synthetic-001"],
    )
    assert summary.scope.student_ids == ("synthetic-001", "synthetic-002")
    assert summary.dimension(Dimension.ALGORITHMS).mean == Fraction(9, 4)
    assert summarize(dataset, batch_id="synthetic-batch", student_ids=[]).scope.student_count == 0
    with pytest.raises(AnalysisInputError, match="unknown_student_in_cohort"):
        summarize(dataset, batch_id="synthetic-batch", student_ids=["synthetic-unseen"])
    with pytest.raises(AnalysisInputError, match="collection"):
        summarize(dataset, batch_id="synthetic-batch", student_ids="synthetic-001")


def test_batch_with_no_records_is_empty(dataset: Dataset) -> None:
    assert summarize(dataset, batch_id="other-batch").scope.student_count == 0


def test_exact_statistics_do_not_depend_on_decimal_context(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    from decimal import Decimal, localcontext

    rows = [
        make_record("synthetic-a", overrides={Dimension.ALGORITHMS: Decimal("2.25")}),
        make_record("synthetic-b", overrides={Dimension.ALGORITHMS: Decimal("4.125")}),
    ]
    with localcontext() as context:
        context.prec = 2
        result = summarize(prepare_dataset(rows, policy=ScorePolicy()), batch_id="synthetic-batch")
    assert result.dimension(Dimension.ALGORITHMS).mean == Fraction(51, 16)
