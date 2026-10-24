"""Scale review/evidence checks; all comments and IDs are generated placeholders."""

import json
from collections import Counter
from dataclasses import replace
from decimal import Decimal

import pytest

from app.analysis.edu import EvidenceKind, prepare_dataset
from app.analysis.edu.comments import CommentInput
from app.analysis.edu.insights import (
    DiscoveryPolicy,
    discover_candidates,
    public_cards,
    teacher_cards,
)

from .scale_data import make_batch
from .test_generality import POLICY, SIZES, A, metrics


def comments_for(data, batch_id):
    comments = []
    for index, row in enumerate(
        record for record in data.records if record.reference.batch_id == batch_id
    ):
        if index % 3 == 0:
            text = "算法基础扎实"  # Synthetic repetition is not a score conflict.
        elif index % 3 == 1:
            units = int(row.score(A).value * 100)
            different = Decimal(units + 1 if units < 500 else 499).scaleb(-2)
            text = f"算法与数据结构评分为{different:.2f}分"
        else:
            text = None
        reference = next(
            ref for ref in row.reference.evidence_refs if ref.kind is EvidenceKind.COMMENT
        )
        comments.append(
            CommentInput(
                row.reference,
                f"{batch_id}-synthetic-comment-{index}",
                reference,
                text,
            )
        )
    return tuple(comments)


@pytest.mark.parametrize("size", SIZES)
def test_review_counts_sources_batch_isolation_and_privacy_at_each_size(size, caplog):
    fixture = make_batch(size, batch_id=f"synthetic-review-{size}")
    foreign = make_batch(17, batch_id=f"synthetic-unrelated-{size}", seed=12)
    data = prepare_dataset((*fixture.rows, *foreign.rows), policy=POLICY)
    comments = comments_for(data, fixture.batch_id)
    foreign_comments = comments_for(data, foreign.batch_id)
    policy_args = dict(
        batch_id=fixture.batch_id,
        snapshot_id="synthetic-review-revision",
        policy=DiscoveryPolicy(include_dimensions=False),
    )
    cards = discover_candidates(data, comments=comments, **policy_args)
    assert cards == discover_candidates(
        data,
        comments=reversed((*comments, *foreign_comments)),
        **policy_args,
    )
    reviews = tuple(card for card in cards if card.candidate_type == "evaluation_review")
    assert reviews
    frequency = Counter(comment.text for comment in comments if comment.text)
    expected_duplicate_groups = {
        frozenset(comment.assessment for comment in comments if comment.text == text): count
        for text, count in frequency.items()
        if count >= 2
    }
    actual_duplicate_groups = {
        frozenset(card.source_refs): metrics(card)["comment_occurrences"].value
        for card in reviews
        if card.status == "duplicate_hint"
    }
    assert actual_duplicate_groups == expected_duplicate_groups
    conflict_refs = {comment.assessment for index, comment in enumerate(comments) if index % 3 == 1}
    actual_conflicts = {
        ref for card in reviews if card.status == "conflict_candidate" for ref in card.source_refs
    }
    assert actual_conflicts == conflict_refs
    repeated_description = {
        comment.assessment for index, comment in enumerate(comments) if index % 3 == 0
    }
    assert not repeated_description & actual_conflicts

    valid_refs = {
        row.reference for row in data.records if row.reference.batch_id == fixture.batch_id
    }
    for card in reviews:
        refs = set(card.source_refs)
        assert refs <= valid_refs
        assert metrics(card)["involved_records"].value == len(refs)
        assert metrics(card)["involved_records"].denominator == size
        assert all(
            link.record in refs and link.reference in link.record.evidence_refs
            for link in card.evidence_refs
        )
        assert "individual_evaluation_review" in card.public_restrictions
    assert (
        public_cards(reviews, approved_ids=frozenset(card.candidate_id for card in reviews)) == ()
    )
    rendered = json.dumps(teacher_cards(cards, authorization_checked=True), ensure_ascii=False)
    assert "synthetic-student-" not in rendered
    assert "synthetic-record-" not in rendered
    assert "synthetic-row-" not in rendered
    assert "算法基础扎实" not in rendered and "算法与数据结构评分为" not in rendered
    assert not caplog.records
    # Raw scores and comments remain immutable; a review never edits them.
    original = {row.student_id: row for row in fixture.rows}
    assert all(
        row.score(A).raw_value == original[row.reference.student_id].scores[A]
        for row in data.records
        if row.reference.batch_id == fixture.batch_id
    )


def test_no_bound_evidence_means_no_invented_candidate_evidence():
    fixture = make_batch(53, batch_id="synthetic-no-evidence")
    rows = tuple(replace(row, evidence_refs=()) for row in fixture.rows)
    cards = discover_candidates(
        prepare_dataset(rows, policy=POLICY),
        batch_id=fixture.batch_id,
        snapshot_id="synthetic-no-evidence-revision",
    )
    assert cards and all(not card.evidence_refs for card in cards)


def test_ai_scope_resource_budget_stays_distinct_from_analysis_size():
    # Read-only regression of an existing safety boundary; no LLM or network client.
    from app.ai.edu.models import InsightError, Limits, SyntheticAccess
    from app.ai.edu.tools import ToolExecutor

    class EmptyProvider:
        def get(self, access, evidence_id):
            return None

    fixture = make_batch(Limits().max_scope_records + 1, batch_id="synthetic-budget")
    data = prepare_dataset(fixture.rows, policy=POLICY)
    access = SyntheticAccess(
        batch_id=fixture.batch_id, student_ids=tuple(row.student_id for row in fixture.rows)
    )
    assert len(data.records) > Limits().max_scope_records
    with pytest.raises(InsightError, match="scope_limit_exceeded"):
        ToolExecutor(data, access, EmptyProvider(), EmptyProvider())
