"""Multi-size integration acceptance with independent integer-hundredth expectations."""

from collections import Counter
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from app.analysis.edu import (
    AnalysisInputError,
    Condition,
    Dimension,
    Operator,
    ScorePolicy,
    prepare_dataset,
    select_students,
    student_profile,
    summarize,
)
from app.analysis.edu.insights import (
    DistributionFocus,
    GroupRule,
    discover_candidates,
    metric_sources,
    public_cards,
    teacher_cards,
)
from app.analysis.edu.models import ScoreState

from .scale_data import make_batch

# These are acceptance inputs, never supported sizes, a roster, or system configuration.
SIZES = (1, 17, 53, 105, 320, 2000)
POLICY = ScorePolicy(minimum=1, maximum=5, step=Fraction(1, 100))
A, S = Dimension.ALGORITHMS, Dimension.STRATEGY


def queries():
    return (
        (
            GroupRule("test.lt", (Condition(S, Operator.GE, 4), Condition(A, Operator.LT, 3))),
            (A, S),
            lambda units: units[S] >= 400 and units[A] < 300,
        ),
        (
            GroupRule("test.le", (Condition(S, Operator.GE, 4), Condition(A, Operator.LE, 3))),
            (A, S),
            lambda units: units[S] >= 400 and units[A] <= 300,
        ),
        (
            GroupRule("test.equal", (Condition(A, Operator.EQ, Fraction(11, 4)),)),
            (A,),
            lambda units: units[A] == 275,
        ),
        (
            GroupRule("test.none", (Condition(A, Operator.LT, 3), Condition(A, Operator.GE, 3))),
            (A,),
            lambda units: False,
        ),
    )


def expected_query(fixture, query, student_ids):
    _, dimensions, predicate = query
    matches, unknown = set(), set()
    for student_id in student_ids:
        units = {
            dimension: fixture.expected[student_id, dimension].units for dimension in dimensions
        }
        if any(value is None for value in units.values()):
            unknown.add(student_id)
        elif predicate(units):
            matches.add(student_id)
    return matches, unknown


def ids(refs):
    return {ref.student_id for ref in refs}


def metrics(card):
    return {metric.key: metric for metric in card.metrics}


def candidates(data, batch_id, *, student_ids=None, comments=None):
    return discover_candidates(
        data,
        batch_id=batch_id,
        snapshot_id=f"{batch_id}-synthetic-revision",
        student_ids=student_ids,
        groups=tuple(query[0] for query in queries()),
        focuses=(DistributionFocus(Condition(A, Operator.LT, 3), Fraction(1, 2)),),
        comments=comments,
    )


def assert_dimension(fixture, data, cards, dimension, student_ids):
    stats = summarize(data, batch_id=fixture.batch_id, student_ids=student_ids).dimension(dimension)
    expected = {sid: fixture.expected[sid, dimension] for sid in student_ids}
    valid = {sid: cell.units for sid, cell in expected.items() if cell.state is ScoreState.VALID}
    missing = {sid for sid, cell in expected.items() if cell.state is ScoreState.MISSING}
    invalid = {sid for sid, cell in expected.items() if cell.state is ScoreState.INVALID}
    n, total = len(valid), len(student_ids)
    ordered_units = sorted(valid.values())
    mean = Fraction(sum(ordered_units), 100 * n) if n else None
    # Integer middle order statistics: no reuse of the engine's statistics.median.
    median = Fraction(ordered_units[(n - 1) // 2] + ordered_units[n // 2], 200) if n else None
    assert (stats.valid_count, stats.missing_count, stats.invalid_count) == (
        n,
        len(missing),
        len(invalid),
    )
    assert stats.valid_count + stats.missing_count + stats.invalid_count == total
    assert (stats.mean, stats.median) == (mean, median)
    assert stats.minimum == (Fraction(min(ordered_units), 100) if n else None)
    assert stats.maximum == (Fraction(max(ordered_units), 100) if n else None)
    assert ids(stats.valid_records) == set(valid)
    frequencies = Counter(ordered_units)
    assert {entry.score: entry.share.count for entry in stats.distribution} == {
        Fraction(units, 100): count for units, count in frequencies.items()
    }
    assert all(entry.share.denominator == n for entry in stats.distribution)
    assert sum((entry.share.proportion for entry in stats.distribution), Fraction()) == (
        1 if n else 0
    )

    card = next(
        card
        for card in cards
        if card.candidate_type == "dimension_distribution" and card.query_ref.dimension is dimension
    )
    ms = metrics(card)
    assert card.denominator == total
    assert (ms["mean"].value, ms["median"].value) == (mean, median)
    assert ms["mean"].denominator == ms["median"].denominator == n
    for key, source_ids in (
        ("valid_count", set(valid)),
        ("mean", set(valid)),
        ("median", set(valid)),
        ("missing_count", missing),
        ("invalid_count", invalid),
    ):
        assert ids(metric_sources(card, key)) == source_ids
    for units, count in frequencies.items():
        value = Fraction(units, 100)
        metric = ms[f"frequency:{value}"]
        assert (metric.value, metric.denominator, metric.at_score) == (count, n, value)
        assert ids(metric.source_refs) == {sid for sid, score in valid.items() if score == units}
    assert card.source_refs == stats.valid_records
    valid_refs = set(stats.valid_records)
    assert all(
        link.record in valid_refs
        and link.reference in link.record.evidence_refs
        and link.reference.dimension is dimension
        for link in card.evidence_refs
    )
    if dimension is A:
        count = sum(score < 300 for score in valid.values())
        assert ms["interval_count"].value == count
        assert ms["interval_count"].denominator == n
        assert ms["interval_ratio"].value == (Fraction(count, n) if n else None)
        assert ids(ms["interval_count"].source_refs) == {
            sid for sid, score in valid.items() if score < 300
        }


def assert_query(fixture, data, cards, query, student_ids):
    rule = query[0]
    matched, unknown = expected_query(fixture, query, student_ids)
    total, eligible = len(student_ids), len(student_ids) - len(unknown)
    result = select_students(
        data,
        batch_id=fixture.batch_id,
        conditions=rule.conditions,
        student_ids=student_ids,
    )
    assert ids(result.matches) == matched
    assert ids(result.unevaluable_records) == unknown
    assert len(result.matches) == len(set(result.matches)) == len(matched)
    assert result.eligible_count == eligible
    assert (result.matched_share.count, result.matched_share.denominator) == (len(matched), total)
    assert result.matched_share.proportion == (Fraction(len(matched), total) if total else None)
    assert result.matched_among_eligible.proportion == (
        Fraction(len(matched), eligible) if eligible else None
    )
    matching_cards = [card for card in cards if card.rule_id == rule.rule_id]
    assert len(matching_cards) == bool(matched)  # No synthetic anomaly for zero matches.
    if matched:
        card = matching_cards[0]
        ms = metrics(card)
        assert card.source_refs == result.matches
        assert card.denominator == total
        assert set(card.conditions) == set(rule.conditions)
        assert ms["matched_count"].value == len(matched)
        assert ms["matched_count"].denominator == ms["matched_ratio"].denominator == total
        assert ms["matched_ratio"].value == Fraction(len(matched), total)
        assert ms["matched_among_eligible"].value == Fraction(len(matched), eligible)
        for key, source_ids in (
            ("matched_count", matched),
            ("matched_ratio", matched),
            ("matched_among_eligible", matched),
            ("eligible_count", set(student_ids) - unknown),
            ("unevaluable_count", unknown),
        ):
            assert ids(metric_sources(card, key)) == source_ids
        matched_refs = set(result.matches)
        assert all(
            link.record in matched_refs
            and link.reference in link.record.evidence_refs
            and link.reference.dimension in query[1]
            for link in card.evidence_refs
        )
    return result


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("with_issues", [False, True], ids=["complete", "missing-invalid"])
def test_scaled_statistics_groups_candidates_and_provenance(size, with_issues):
    fixture = make_batch(
        size, batch_id=f"synthetic-scale-{size}-issues-{with_issues}", with_issues=with_issues
    )
    data = prepare_dataset(fixture.rows, policy=POLICY)
    student_ids = tuple(row.student_id for row in fixture.rows)
    cards = candidates(data, fixture.batch_id)
    assert summarize(data, batch_id=fixture.batch_id).scope.student_count == size
    assert len(tuple(Dimension)) == 5
    assert all(card.batch_id == fixture.batch_id and card.denominator == size for card in cards)
    for dimension in Dimension:
        assert_dimension(fixture, data, cards, dimension, student_ids)
    selections = [assert_query(fixture, data, cards, query, student_ids) for query in queries()]
    # <=3 adds exactly the evaluable strategy>=4 records whose algorithm score is exactly 3.
    added = ids(selections[1].matches) - ids(selections[0].matches)
    assert added == {
        sid
        for sid in student_ids
        if fixture.expected[sid, A].units == 300
        and fixture.expected[sid, S].units is not None
        and fixture.expected[sid, S].units >= 400
    }
    assert ids(selections[0].matches) <= ids(selections[1].matches)
    profile = student_profile(data, batch_id=fixture.batch_id, student_id=student_ids[0])
    assert profile.reference.student_id == student_ids[0]
    for dimension, item in zip(Dimension, profile.dimensions, strict=True):
        cell = fixture.expected[student_ids[0], dimension]
        assert item.score.state is cell.state
        expected_score = Fraction(cell.units, 100) if cell.units is not None else None
        assert item.score.value == expected_score
        mean = summarize(data, batch_id=fixture.batch_id).dimension(dimension).mean
        assert item.difference_from_mean == (
            expected_score - mean if expected_score is not None and mean is not None else None
        )
    # No raw fixture identity or source locator can be exported as a public detail object.
    assert public_cards(cards) == ()
    assert len(teacher_cards(cards, authorization_checked=True)) == len(cards)


@pytest.mark.parametrize("size", SIZES)
def test_batches_subsets_reordering_and_changed_scores(size):
    fixture = make_batch(size, batch_id=f"synthetic-primary-{size}", with_issues=True)
    foreign = make_batch(size + 3, batch_id=f"synthetic-foreign-{size}", seed=129)
    data = prepare_dataset((*foreign.rows, *reversed(fixture.rows)), policy=POLICY)
    standalone = prepare_dataset(fixture.rows, policy=POLICY)
    assert summarize(data, batch_id=fixture.batch_id) == summarize(
        standalone,
        batch_id=fixture.batch_id,
    )
    primary_cards = candidates(data, fixture.batch_id)
    assert primary_cards == candidates(standalone, fixture.batch_id)
    assert all(
        ref.batch_id == fixture.batch_id
        for card in primary_cards
        for ref in (*card.query_ref.scope.records, *card.source_refs)
    )
    assert summarize(data, batch_id=foreign.batch_id).scope.student_count == size + 3
    subset = tuple(row.student_id for row in fixture.rows[::3])
    # Repeated requested IDs are a set of students, not additional observations.
    requested = (*reversed(subset), subset[0])
    subset_cards = candidates(data, fixture.batch_id, student_ids=requested)
    assert subset_cards == candidates(data, fixture.batch_id, student_ids=subset)
    assert all(card.denominator == len(subset) for card in subset_cards)
    for dimension in Dimension:
        assert_dimension(fixture, data, subset_cards, dimension, subset)
    for query in queries():
        assert_query(fixture, data, subset_cards, query, subset)
    with pytest.raises(AnalysisInputError, match="unknown_student_in_cohort"):
        candidates(data, fixture.batch_id, student_ids=("synthetic-not-in-this-batch",))

    low_rows = [
        replace(row, scores={dimension: Decimal("1.00") for dimension in Dimension})
        for row in fixture.rows
    ]
    high_rows = [
        replace(row, scores={dimension: Decimal("5.00") for dimension in Dimension})
        for row in fixture.rows
    ]
    low, high = (prepare_dataset(rows, policy=POLICY) for rows in (low_rows, high_rows))
    condition = (Condition(A, Operator.LT, 3),)
    assert (
        len(select_students(low, batch_id=fixture.batch_id, conditions=condition).matches) == size
    )
    assert select_students(high, batch_id=fixture.batch_id, conditions=condition).matches == ()
    assert summarize(low, batch_id=fixture.batch_id).dimension(A).mean == 1
    assert summarize(high, batch_id=fixture.batch_id).dimension(A).mean == 5
    high_cards = discover_candidates(
        high,
        batch_id=fixture.batch_id,
        snapshot_id="synthetic-changed-revision",
        groups=(GroupRule("test.low", condition),),
    )
    assert all(card.candidate_type != "ability_group" for card in high_cards)
    assert all(metrics(card)["mean"].value == 5 for card in high_cards)


def test_empty_unknown_batch_all_missing_and_duplicate_record_inputs():
    empty = prepare_dataset((), policy=POLICY)
    batch_id = "synthetic-empty"
    assert summarize(empty, batch_id=batch_id).scope.student_count == 0
    assert candidates(empty, batch_id) == ()
    for query in queries():
        result = select_students(empty, batch_id=batch_id, conditions=query[0].conditions)
        assert result.matches == result.unevaluable_records == ()
        assert result.matched_share.proportion is result.matched_among_eligible.proportion is None

    fixture = make_batch(17, batch_id="synthetic-present")
    data = prepare_dataset(fixture.rows, policy=POLICY)
    # The core has no database and treats a batch absent from its input as empty.
    assert candidates(data, batch_id) == ()
    assert candidates(data, fixture.batch_id, student_ids=()) == ()
    with pytest.raises(AnalysisInputError, match="duplicate_student_in_batch"):
        prepare_dataset((*fixture.rows, fixture.rows[0]), policy=POLICY)

    missing_rows = [
        replace(row, scores={dimension: None for dimension in Dimension}) for row in fixture.rows
    ]
    all_missing = prepare_dataset(missing_rows, policy=POLICY)
    cards = candidates(all_missing, fixture.batch_id)
    assert len(cards) == 5
    assert all(card.status == "insufficient_data" for card in cards)
    assert all(metrics(card)["mean"].value is None for card in cards)
    assert all(not card.source_refs and not card.evidence_refs for card in cards)
    assert all(
        metrics(card)["interval_ratio"].value is None
        for card in cards
        if card.query_ref.dimension is A
    )


def test_hundredth_precision_entire_legal_grid_and_invalid_cells():
    fixture = make_batch(401, batch_id="synthetic-hundredths")
    grid = tuple(
        replace(row, scores={dimension: Decimal(units).scaleb(-2) for dimension in Dimension})
        for row, units in zip(fixture.rows, range(100, 501), strict=True)
    )
    data = prepare_dataset(grid, policy=POLICY)
    assert not data.issues
    for stats in summarize(data, batch_id=fixture.batch_id).dimensions:
        assert stats.valid_count == 401
        assert len(stats.distribution) == 401
        assert stats.mean == stats.median == 3
        assert (stats.minimum, stats.maximum) == (1, 5)
    bad_cells = (Decimal("0.99"), Decimal("5.01"), Decimal("2.755"))
    for raw in bad_cells:
        row = replace(grid[0], scores={A: raw})
        invalid = prepare_dataset((row,), policy=POLICY)
        score = invalid.records[0].score(A)
        assert score.state is ScoreState.INVALID and score.value is None
        assert score.raw_value == raw  # Never round off-step data into a legal score.


@pytest.mark.parametrize("size", SIZES)
def test_spearman_uses_actual_pair_count_and_ties_at_each_size(size):
    from app.analysis.edu import spearman_correlations
    from app.analysis.edu.models import CorrelationState

    fixture = make_batch(size, batch_id=f"synthetic-ranks-{size}")
    rows = tuple(
        replace(
            row,
            scores={
                **row.scores,
                Dimension.IMPLEMENTATION: Decimal("6.00") - row.scores[A],
            },
        )
        for row in fixture.rows
    )
    data = prepare_dataset(rows, policy=POLICY)
    pair = spearman_correlations(data, batch_id=fixture.batch_id).pairs[0]
    assert pair.paired_count == size and not pair.excluded_records
    assert ids(observation.reference for observation in pair.observations) == {
        row.student_id for row in rows
    }
    if size == 1:
        assert pair.state is CorrelationState.INSUFFICIENT_PAIRS
        assert pair.coefficient is None
    else:
        # Monotone reverse of the actual algorithm scores, including all repeated values.
        assert pair.state is CorrelationState.COMPUTABLE
        assert pair.coefficient == pytest.approx(-1)
