from collections.abc import Iterable, Sequence
from fractions import Fraction
from itertools import combinations
from statistics import correlation

from .engine import cohort
from .models import (
    AnalysisInputError,
    Correlations,
    CorrelationState,
    Dataset,
    Dimension,
    PairCorrelation,
    RankedPair,
    ScoreState,
    ValidatedRecord,
)


def average_ranks(values: Sequence[Fraction]) -> tuple[float, ...]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        stop = start + 1
        while stop < len(ordered) and values[ordered[stop]] == values[ordered[start]]:
            stop += 1
        rank = (start + 1 + stop) / 2
        for position in ordered[start:stop]:
            ranks[position] = rank
        start = stop
    return tuple(ranks)


def _pair(
    records: Sequence[ValidatedRecord], left: Dimension, right: Dimension, minimum_pairs: int
) -> PairCorrelation:
    included = []
    excluded = []
    for record in records:
        x, y = record.score(left).value, record.score(right).value
        if record.score(left).state is ScoreState.VALID and (
            record.score(right).state is ScoreState.VALID
        ):
            if x is None or y is None:
                raise AnalysisInputError("invalid_prepared_dataset")
            included.append((record.reference, x, y))
        else:
            excluded.append(record.reference)
    x_values = tuple(item[1] for item in included)
    y_values = tuple(item[2] for item in included)
    x_ranks, y_ranks = average_ranks(x_values), average_ranks(y_values)
    observations = tuple(
        RankedPair(reference, x, y, x_rank, y_rank)
        for (reference, x, y), x_rank, y_rank in zip(included, x_ranks, y_ranks, strict=True)
    )
    coefficient = None
    if len(included) < minimum_pairs:
        state = CorrelationState.INSUFFICIENT_PAIRS
    elif len(set(x_values)) == 1 or len(set(y_values)) == 1:
        state = CorrelationState.CONSTANT_INPUT
    else:
        state = CorrelationState.COMPUTABLE
        # Pearson on average ranks handles ties; the no-ties d² shortcut does not.
        coefficient = correlation(x_ranks, y_ranks)
    return PairCorrelation(left, right, state, coefficient, observations, tuple(excluded))


def spearman_correlations(
    dataset: Dataset,
    *,
    batch_id: str,
    student_ids: Iterable[str] | None = None,
    minimum_pairs: int = 2,
) -> Correlations:
    if isinstance(minimum_pairs, bool) or not isinstance(minimum_pairs, int) or minimum_pairs < 2:
        raise AnalysisInputError("minimum_pairs_must_be_an_integer_at_least_two")
    scope, records = cohort(dataset, batch_id, student_ids)
    pairs = tuple(
        _pair(records, left, right, minimum_pairs) for left, right in combinations(Dimension, 2)
    )
    return Correlations(scope, minimum_pairs, pairs)


def dimension_pair(
    dataset: Dataset,
    *,
    batch_id: str,
    left: Dimension,
    right: Dimension,
    student_ids: Iterable[str] | None = None,
    minimum_pairs: int = 2,
) -> PairCorrelation:
    """Target one pair with exactly the same ranking/validation as the matrix."""
    if not isinstance(left, Dimension) or not isinstance(right, Dimension) or left is right:
        raise AnalysisInputError("two_distinct_dimensions_required")
    if type(minimum_pairs) is not int or minimum_pairs < 2:
        raise AnalysisInputError("minimum_pairs_must_be_an_integer_at_least_two")
    _, records = cohort(dataset, batch_id, student_ids)
    ordered = sorted((left, right), key=list(Dimension).index)
    return _pair(records, *ordered, minimum_pairs)
