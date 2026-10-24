"""Three kinds of fully synthetic cards, with no model calls or teacher file access."""

import json
from fractions import Fraction

from .. import (
    AssessmentRecord,
    Condition,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    Operator,
    ScorePolicy,
    prepare_dataset,
)
from ..comments import CommentInput
from . import DistributionFocus, GroupRule, InsightCandidate, discover_candidates, teacher_cards


def synthetic_candidates() -> tuple[InsightCandidate, ...]:
    rows = []
    for index, (algorithm, strategy) in enumerate([(2, 4), (2.75, 5), (3, 3), (4, 4)]):
        refs = (
            EvidenceRef(
                "synthetic-source",
                f"synthetic-row-{index}/score",
                EvidenceKind.SCORE,
                Dimension.ALGORITHMS,
            ),
            EvidenceRef(
                "synthetic-source",
                f"synthetic-row-{index}/comment",
                EvidenceKind.COMMENT,
                Dimension.ALGORITHMS,
            ),
        )
        rows.append(
            AssessmentRecord(
                "synthetic-batch",
                f"synthetic-student-{index}",
                f"synthetic-row-{index}",
                "synthetic-source",
                {
                    dimension: algorithm
                    if dimension is Dimension.ALGORITHMS
                    else strategy
                    if dimension is Dimension.STRATEGY
                    else 3
                    for dimension in Dimension
                },
                refs,
            )
        )
    data = prepare_dataset(rows, policy=ScorePolicy(step=Fraction(1, 100)))
    # Repetition and conflict are independent review signals, never automatic score changes.
    texts = ["算法基础扎实", "算法基础扎实", "算法与数据结构评分为5分", None]
    comments = tuple(
        CommentInput(
            record.reference,
            f"synthetic-comment-{index}",
            record.reference.evidence_refs[1],
            texts[index],
        )
        for index, record in enumerate(data.records)
    )
    return discover_candidates(
        data,
        batch_id="synthetic-batch",
        snapshot_id="synthetic-snapshot-v1",
        groups=(
            GroupRule(
                "explore.strategy_algorithm",
                (
                    Condition(Dimension.STRATEGY, Operator.GE, Fraction(4)),
                    Condition(Dimension.ALGORITHMS, Operator.LT, Fraction(3)),
                ),
            ),
        ),
        focuses=(
            DistributionFocus(
                Condition(Dimension.ALGORITHMS, Operator.LT, Fraction(3)),
                Fraction(1, 2),
            ),
        ),
        comments=comments,
    )


def main() -> None:
    candidates = synthetic_candidates()
    chosen = (
        next(
            item
            for item in candidates
            if item.candidate_type == "dimension_distribution"
            and item.query_ref.dimension is Dimension.ALGORITHMS
        ),
        next(item for item in candidates if item.candidate_type == "ability_group"),
        next(item for item in candidates if item.status == "duplicate_hint"),
    )
    print(
        json.dumps(
            {
                "data_kind": "synthetic_only",
                "teacher_authorization": "synthetic_local_demo_not_real_auth",
                "cards": teacher_cards(chosen, authorization_checked=True),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
