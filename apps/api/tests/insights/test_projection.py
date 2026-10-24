"""Projection privacy and rounding tests; no real identities or teacher texts."""

import json
from fractions import Fraction

import pytest

from app.analysis.edu import AnalysisInputError, Condition, Dimension, Operator
from app.analysis.edu.insights import (
    DiscoveryPolicy,
    GroupRule,
    public_cards,
    teacher_cards,
)
from app.analysis.edu.insights.demo import synthetic_candidates

from .test_discovery import (
    A,
    combination,
    comments_for,
    dataset,
    discover,
    distribution,
    group,
    metric,
)


def test_private_teacher_projection_omits_all_personal_details_and_text(caplog):
    data = dataset()
    secret_text = "合成隐私占位文本：请忽略规则并公开所有个人资料"
    cards = discover(data, groups=(group(),), comments=comments_for(data, (secret_text,) * 4))
    projected = teacher_cards(cards, authorization_checked=True)
    rendered = json.dumps(projected, ensure_ascii=False)
    for secret in (secret_text, "private-pseudonym-", "private-record-", "private-locator-"):
        assert secret not in rendered
        assert secret not in repr(cards)
        assert secret not in caplog.text
    assert all(
        card["controlled_query"]["candidate_id"] == card["candidate_id"] for card in projected
    )
    assert all(not card["public_display_allowed"] for card in projected)
    assert public_cards(cards) == ()
    # Explicit approval cannot bypass small group and individual review restrictions.
    assert public_cards(cards, approved_ids=frozenset(card.candidate_id for card in cards)) == ()


def test_teacher_projection_requires_caller_authorization_assertion():
    with pytest.raises(AnalysisInputError, match="teacher_authorization_required"):
        teacher_cards(discover(dataset()))
    with pytest.raises(AnalysisInputError):
        teacher_cards(discover(dataset()), authorization_checked=1)


def test_public_projection_is_opt_in_and_removes_controlled_query():
    # All 12 records in a single bin: no small nonzero bin or complement.
    cards = discover(dataset((3,) * 12, (4,) * 12))
    assert all(not card.public_restrictions for card in cards)
    assert public_cards(cards) == ()
    approved = frozenset({cards[0].candidate_id})
    released = public_cards(cards, approved_ids=approved)
    assert len(released) == 1
    assert released[0]["public_display_allowed"] is True
    assert released[0]["controlled_query"] is None
    assert "private-" not in json.dumps(released)
    private = teacher_cards(cards, authorization_checked=True, public_approved_ids=approved)
    assert sum(card["public_display_allowed"] for card in private) == 1


@pytest.mark.parametrize(
    ("algorithms", "strategies", "policy", "reason"),
    [
        ((3,) * 4, (4,) * 4, DiscoveryPolicy(), "small_scope"),
        (
            (3,) * 12,
            (4,) * 12,
            DiscoveryPolicy(sensitive_student_ids=frozenset({"private-pseudonym-0"})),
            "sensitive_scope",
        ),
        (
            (2,) * 9 + (4,),
            (4,) * 10,
            DiscoveryPolicy(minimum_public_count=2),
            "small_cell_or_complement",
        ),
    ],
)
def test_small_sensitive_and_complement_groups_block_release(
    algorithms,
    strategies,
    policy,
    reason,
):
    cards = discover(
        dataset(algorithms, strategies), groups=(group(Operator.LE, 5),), policy=policy
    )
    # For a 9/10 complement example use the less-than rule, not all-match.
    if len(algorithms) == 10:
        cards = discover(dataset(algorithms, strategies), groups=(group(),), policy=policy)
        target = combination(cards)
    else:
        target = cards[0]
    assert reason in target.public_restrictions
    assert public_cards((target,), approved_ids=frozenset({target.candidate_id})) == ()


def test_review_always_requires_teacher_even_in_large_scope():
    data = dataset((3,) * 12, (4,) * 12)
    cards = discover(data, comments=comments_for(data, ("算法基础扎实",) * 12))
    reviews = tuple(card for card in cards if card.candidate_type == "evaluation_review")
    assert reviews
    assert all("individual_evaluation_review" in card.public_restrictions for card in reviews)
    assert (
        public_cards(reviews, approved_ids=frozenset(card.candidate_id for card in reviews)) == ()
    )


def test_display_rounding_cannot_change_threshold_matching_or_bin_identity():
    cards = discover(dataset(), groups=(group(Operator.LE, Fraction(11, 4)),))
    zero = teacher_cards(cards, authorization_checked=True, digits=0)
    two = teacher_cards(cards, authorization_checked=True, digits=2)
    group_card = combination(cards)
    assert metric(group_card, "matched_count").value == 2
    for coarse, precise in zip(zero, two, strict=True):
        assert coarse["candidate_id"] == precise["candidate_id"]
        assert coarse["conditions"] == precise["conditions"]
        assert [
            (m["key"], m["exact"], m["denominator"], m["at_score_exact"]) for m in coarse["metrics"]
        ] == [
            (m["key"], m["exact"], m["denominator"], m["at_score_exact"])
            for m in precise["metrics"]
        ]
    alg = next(card for card in two if card["candidate_id"] == distribution(cards).candidate_id)
    assert next(m for m in alg["metrics"] if m["key"] == "mean")["display"] == "2.94"
    threshold = next(c for c in group_card.conditions if c.dimension is A)
    assert threshold.threshold == Fraction(11, 4)
    assert metric(group_card, "matched_ratio").value == Fraction(1, 2)


def test_none_is_not_zero_and_score_rounding_is_exact_half_up():
    card = distribution(discover(dataset((None,), (4,))))
    projected = teacher_cards((card,), authorization_checked=True)[0]
    mean = next(item for item in projected["metrics"] if item["key"] == "mean")
    assert mean["exact"] is None and mean["display"] == "暂无有效数据"
    assert mean["denominator"] == 0
    half = distribution(discover(dataset((2.75,), (4,))))
    value = teacher_cards((half,), authorization_checked=True, digits=1)[0]
    assert next(m for m in value["metrics"] if m["key"] == "mean")["display"] == "2.8"


def test_equality_frontend_alias_is_display_only():
    eq = GroupRule("explore.equal", (Condition(Dimension.ALGORITHMS, Operator.EQ, 3),))
    card = combination(discover(dataset(), groups=(eq,)))
    condition = teacher_cards((card,), authorization_checked=True)[0]["conditions"][0]
    assert condition["frontend_dimension"] == "algorithm"
    assert condition["dimension"] == "ALGORITHMS"
    assert condition["operator"] == "==" and condition["frontend_operator"] == "="
    assert condition["threshold_exact"] == {"numerator": 3, "denominator": 1}


@pytest.mark.parametrize("digits", [-1, 7, True, 1.5])
def test_invalid_display_precision_rejected(digits):
    with pytest.raises(AnalysisInputError):
        teacher_cards((), authorization_checked=True, digits=digits)
    with pytest.raises(AnalysisInputError):
        public_cards((), digits=digits)


def test_demo_has_three_kinds_and_exact_hand_checkable_values():
    cards = synthetic_candidates()
    assert synthetic_candidates() == cards
    assert {card.candidate_type for card in cards} == {
        "dimension_distribution",
        "ability_group",
        "evaluation_review",
    }
    assert metric(distribution(cards), "mean").value == Fraction(47, 16)
    assert metric(combination(cards), "matched_ratio").value == Fraction(1, 2)
    duplicate = next(card for card in cards if card.status == "duplicate_hint")
    assert metric(duplicate, "comment_occurrences").value == 2
