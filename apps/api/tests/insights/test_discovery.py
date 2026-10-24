"""Synthetic integration cases for candidate calculation and exact provenance."""

from dataclasses import replace
from fractions import Fraction

import pytest

from app.analysis.edu import (
    AnalysisInputError,
    AssessmentRecord,
    Condition,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    Operator,
    ScorePolicy,
    prepare_dataset,
)
from app.analysis.edu.comments import CommentInput
from app.analysis.edu.insights import (
    DiscoveryPolicy,
    DistributionFocus,
    GroupRule,
    discover_candidates,
    metric_sources,
)

A = Dimension.ALGORITHMS
S = Dimension.STRATEGY


def dataset(algorithms=(2, 2.75, 3, 4), strategies=(4, 5, 4, 4), *, batch="synthetic-batch"):
    rows = []
    for index, (algorithm, strategy) in enumerate(zip(algorithms, strategies, strict=True)):
        refs = tuple(
            EvidenceRef(
                "synthetic-source",
                f"private-locator-{index}-{dimension.name}-{kind.value}",
                kind,
                dimension,
            )
            for dimension in (A, S)
            for kind in (EvidenceKind.SCORE, EvidenceKind.COMMENT)
        )
        rows.append(
            AssessmentRecord(
                batch,
                f"private-pseudonym-{index}",
                f"private-record-{index}",
                "synthetic-source",
                {
                    dimension: algorithm if dimension is A else strategy if dimension is S else 3
                    for dimension in Dimension
                },
                refs,
            )
        )
    return prepare_dataset(rows, policy=ScorePolicy(step=Fraction(1, 100)))


def group(operator=Operator.LT, threshold=3):
    return GroupRule(
        "explore.synthetic",
        (
            Condition(S, Operator.GE, 4),
            Condition(A, operator, threshold),
        ),
    )


def discover(data, **kwargs):
    return discover_candidates(
        data,
        batch_id="synthetic-batch",
        snapshot_id="synthetic-snapshot-v1",
        **kwargs,
    )


def metric(candidate, key):
    return next(item for item in candidate.metrics if item.key == key)


def distribution(candidates, dimension=A):
    return next(
        item
        for item in candidates
        if item.candidate_type == "dimension_distribution" and item.query_ref.dimension is dimension
    )


def combination(candidates):
    return next(item for item in candidates if item.candidate_type == "ability_group")


def comments_for(data, texts):
    return tuple(
        CommentInput(
            row.reference,
            f"synthetic-comment-{index}",
            next(
                ref
                for ref in row.reference.evidence_refs
                if ref.kind is EvidenceKind.COMMENT and ref.dimension is A
            ),
            text,
        )
        for index, (row, text) in enumerate(zip(data.records, texts, strict=True))
    )


def test_five_dimensions_with_hand_calculated_fractional_statistics():
    cards = discover(dataset())
    assert len(cards) == 5
    card = distribution(cards)
    # (2 + 11/4 + 3 + 4) / 4 = 47/16; middle pair (11/4 + 3)/2 = 23/8.
    assert metric(card, "mean").value == Fraction(47, 16)
    assert metric(card, "median").value == Fraction(23, 8)
    assert metric(card, "valid_count").value == card.denominator == 4
    frequencies = [m for m in card.metrics if m.at_score is not None]
    assert [(m.at_score, m.value, m.denominator) for m in frequencies] == [
        (Fraction(2), 1, 4),
        (Fraction(11, 4), 1, 4),
        (Fraction(3), 1, 4),
        (Fraction(4), 1, 4),
    ]
    assert metric(distribution(cards, Dimension.MODELING), "mean").value == 3


def test_configurable_concentration_does_not_claim_ability_deficit():
    card = distribution(
        discover(
            dataset(), focuses=(DistributionFocus(Condition(A, Operator.LT, 3), Fraction(1, 2)),)
        )
    )
    assert metric(card, "interval_count").value == 2
    assert metric(card, "interval_ratio").value == Fraction(1, 2)
    assert "较集中" in card.title
    assert "低分不等于能力不足" in "".join(card.limitations)
    ordinary = distribution(
        discover(
            dataset(), focuses=(DistributionFocus(Condition(A, Operator.LT, 3), Fraction(3, 4)),)
        )
    )
    assert "较集中" not in ordinary.title


@pytest.mark.parametrize(
    ("operator", "threshold", "expected"),
    [
        (Operator.LT, 3, 2),
        (Operator.LE, 3, 3),
        (Operator.LT, Fraction(11, 4), 1),
        (Operator.LE, Fraction(11, 4), 2),
        (Operator.EQ, Fraction(11, 4), 1),
    ],
)
def test_and_filter_exact_boundaries(operator, threshold, expected):
    card = combination(discover(dataset(), groups=(group(operator, threshold),)))
    assert metric(card, "matched_count").value == expected
    assert metric(card, "matched_ratio").value == Fraction(expected, 4)
    assert metric(card, "matched_among_eligible").value == Fraction(expected, 4)
    assert metric(card, "eligible_count").value == 4
    assert len(card.source_refs) == len(set(card.source_refs)) == expected


def test_missing_invalid_and_two_different_denominators():
    data = dataset((2, None, "invalid synthetic value", 4))
    cards = discover(data, groups=(group(),))
    stats, matched = distribution(cards), combination(cards)
    # Only 2 and 4 contribute: mean = median = 3, M/N=1/4, M/E=1/2.
    assert metric(stats, "mean").value == metric(stats, "median").value == 3
    assert metric(stats, "mean").denominator == 2
    assert metric(stats, "missing_count").value == metric(stats, "invalid_count").value == 1
    assert metric(matched, "matched_ratio").value == Fraction(1, 4)
    assert metric(matched, "matched_among_eligible").value == Fraction(1, 2)
    assert metric(matched, "unevaluable_count").value == 2
    valid_ids = {"private-record-0", "private-record-3"}
    assert {ref.record_id for ref in metric_sources(stats, "mean")} == valid_ids
    assert {ref.record_id for ref in metric_sources(stats, "median")} == valid_ids
    assert {link.record.record_id for link in stats.evidence_refs} == valid_ids
    assert [ref.record_id for ref in matched.source_refs] == ["private-record-0"]
    assert {link.record.record_id for link in matched.evidence_refs} == {"private-record-0"}
    assert {ref.record_id for ref in metric_sources(matched, "eligible_count")} == valid_ids
    assert {ref.record_id for ref in metric_sources(matched, "unevaluable_count")} == {
        "private-record-1",
        "private-record-2",
    }
    # No mutation or silent score correction.
    assert data.records[2].score(A).raw_value == "invalid synthetic value"


def test_frequency_provenance_is_exact_cell_not_entire_cohort():
    data = dataset()
    card = distribution(discover(data))
    refs = metric_sources(card, "frequency:11/4")
    assert refs == (data.records[1].reference,)
    assert len(metric_sources(card, "mean")) == 4
    assert all(
        link.reference.kind is EvidenceKind.SCORE
        and link.reference.dimension is A
        and link.reference in link.record.evidence_refs
        for link in card.evidence_refs
    )
    with pytest.raises(AnalysisInputError, match="unknown_candidate_metric"):
        metric_sources(card, "not-a-metric")


def test_zero_matches_empty_scope_and_no_configured_candidate():
    no_stats = DiscoveryPolicy(include_dimensions=False)
    assert discover(dataset(), groups=(group(Operator.GT, 5),), policy=no_stats) == ()
    assert discover(dataset(), student_ids=(), groups=(group(),)) == ()
    empty = prepare_dataset([], policy=ScorePolicy(step=Fraction(1, 100)))
    assert discover(empty, groups=(group(),), comments=()) == ()
    assert discover(dataset(), policy=no_stats) == ()


def test_single_record_and_no_valid_dimension():
    single = distribution(discover(dataset((2.75,), (4,))))
    assert metric(single, "mean").value == Fraction(11, 4)
    assert metric(single, "mean").denominator == 1
    none = distribution(discover(dataset((None, "bad"), (4, 5))))
    assert none.status == "insufficient_data"
    assert metric(none, "mean").value is None
    assert metric(none, "median").value is None
    assert not none.source_refs and not none.evidence_refs


def test_batch_and_requested_scope_isolation():
    data, other = dataset(), dataset((1,), (5,), batch="synthetic-other")
    combined = replace(data, records=(*data.records, *other.records))
    cards = discover(combined, groups=(group(),), student_ids=("private-pseudonym-1",))
    assert all(card.denominator == 1 for card in cards)
    assert all(ref.batch_id == "synthetic-batch" for card in cards for ref in card.source_refs)
    assert metric(distribution(cards), "mean").value == Fraction(11, 4)
    with pytest.raises(AnalysisInputError):
        discover(combined, student_ids=("unknown-student",))


def test_order_independent_repeatable_output_and_snapshot_identity():
    data = dataset()
    comments = comments_for(data, ("算法基础扎实", "算法基础扎实", None, None))
    first = discover(data, groups=(group(),), comments=comments)
    reverse_group = GroupRule(group().rule_id, tuple(reversed(group().conditions)))
    assert first == discover(data, groups=(group(),), comments=comments)
    assert first == discover(
        prepare_dataset(
            [
                AssessmentRecord(
                    row.reference.batch_id,
                    row.reference.student_id,
                    row.reference.record_id,
                    row.reference.source_id,
                    {score.dimension: score.raw_value for score in row.scores},
                    row.reference.evidence_refs,
                )
                for row in reversed(data.records)
            ],
            policy=data.policy,
        ),
        groups=(reverse_group,),
        comments=reversed(comments),
    )
    other = discover_candidates(
        data,
        batch_id="synthetic-batch",
        snapshot_id="synthetic-snapshot-v2",
        groups=(group(),),
        comments=comments,
    )
    assert not {card.candidate_id for card in first} & {card.candidate_id for card in other}


def test_duplicate_text_is_review_hint_not_wrong_score():
    data = dataset()
    comments = comments_for(data, ("算法基础扎实", "算法基础扎实", None, None))
    cards = discover(data, comments=comments)
    duplicate = next(card for card in cards if card.status == "duplicate_hint")
    assert metric(duplicate, "comment_occurrences").value == 2
    assert metric(duplicate, "involved_records").value == 2
    assert metric(duplicate, "involved_records").denominator == 4
    assert len(duplicate.source_refs) == 2
    assert all(link.reference.kind is EvidenceKind.COMMENT for link in duplicate.evidence_refs)
    assert duplicate.source_rule_fingerprint
    assert not any(card.status == "conflict_candidate" for card in cards)
    assert "重复仅为检查线索" in duplicate.summary
    assert [row.score(A).value for row in data.records] == [2, Fraction(11, 4), 3, 4]


def test_strict_numeric_conflict_and_empty_comment_keep_correct_sources():
    data = dataset()
    comments = comments_for(
        data,
        (
            "算法与数据结构评分为5分",
            "算法与数据结构评分为2.75分",
            None,
            "算法基础扎实",
        ),
    )
    cards = discover(data, comments=comments)
    conflicts = [card for card in cards if card.status == "conflict_candidate"]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.source_refs == (data.records[0].reference,)
    assert metric(conflict, "involved_records").value == 1
    assert {link.reference.kind for link in conflict.evidence_refs} == {
        EvidenceKind.COMMENT,
        EvidenceKind.SCORE,
    }
    assert all(link.record == data.records[0].reference for link in conflict.evidence_refs)
    assert any(
        card.status == "needs_manual_review" and data.records[2].reference in card.source_refs
        for card in cards
    )
    assert all(
        "individual_evaluation_review" in card.public_restrictions
        for card in cards
        if card.candidate_type == "evaluation_review"
    )


def test_provider_not_supplied_does_not_imply_empty_teacher_comments():
    data = dataset()
    assert not any(card.candidate_type == "evaluation_review" for card in discover(data))
    explicit_absence = discover(data, comments=())
    assert any(card.candidate_type == "evaluation_review" for card in explicit_absence)
    assert all(card.status != "conflict_candidate" for card in explicit_absence)


def test_forged_comment_record_or_evidence_is_rejected():
    data = dataset()
    valid = comments_for(data, ("算法基础扎实", None, None, None))[0]
    wrong_record = replace(valid, assessment=data.records[1].reference)
    foreign_ref = EvidenceRef("synthetic-source", "nonexistent-evidence", EvidenceKind.COMMENT, A)
    with pytest.raises(AnalysisInputError):
        discover(data, comments=(wrong_record,))
    with pytest.raises(AnalysisInputError):
        discover(data, comments=(replace(valid, evidence=foreign_ref),))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: GroupRule("unsafe rule", (Condition(A, Operator.LT, 3),)),
        lambda: GroupRule(None, (Condition(A, Operator.LT, 3),)),
        lambda: GroupRule("valid", ()),
        lambda: DistributionFocus(Condition(A, Operator.LT, 3), 0),
        lambda: DistributionFocus(Condition(A, Operator.LT, 3), Fraction(3, 2)),
        lambda: DiscoveryPolicy(minimum_public_count=1),
        lambda: DiscoveryPolicy(minimum_public_count=True),
        lambda: DiscoveryPolicy(sensitive_student_ids=["private-pseudonym-0"]),
    ],
)
def test_invalid_configuration_fails_explicitly(factory):
    with pytest.raises(AnalysisInputError):
        factory()


def test_duplicate_rules_or_focuses_fail_instead_of_double_counting():
    data = dataset()
    focus = DistributionFocus(Condition(A, Operator.LT, 3), Fraction(1, 2))
    with pytest.raises(AnalysisInputError):
        discover(data, groups=(group(), group()))
    with pytest.raises(AnalysisInputError):
        discover(data, focuses=(focus, focus))
    with pytest.raises(AnalysisInputError):
        discover(data, policy=False)
