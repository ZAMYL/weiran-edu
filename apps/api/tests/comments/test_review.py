from collections.abc import Callable

import pytest

from app.analysis.edu import AssessmentRecord, Dimension, ScorePolicy, prepare_dataset
from app.analysis.edu.comments import (
    CommentInput,
    FindingKind,
    ReviewState,
    review_comments,
)

CaseFactory = Callable[..., tuple[AssessmentRecord, CommentInput]]


@pytest.mark.parametrize("dimension", list(Dimension))
def test_explicit_numeric_conflict_candidate_has_exact_evidence(
    make_case: CaseFactory,
    dimension: Dimension,
) -> None:
    row, comment = make_case(f"{dimension.value}评分为5分", score=2, dimension=dimension)
    data = prepare_dataset([row], policy=ScorePolicy())
    result = review_comments(data, [comment], batch_id=row.batch_id)
    review = result.records[0]
    assert review.state is ReviewState.CANDIDATE
    assert len(review.findings) == 1
    finding = review.findings[0]
    assert finding.rule_id == "review.numeric_mismatch"
    assert finding.kind is FindingKind.SCORE_CONFLICT
    assert finding.reason_code == "explicit_stated_score_differs_from_record"
    assert finding.dimension is dimension
    assert (finding.recorded_score, finding.stated_score) == (2, 5)
    assert finding.assessments == (comment.assessment,)
    assert finding.comments[0].evidence == comment.evidence
    assert finding.score_evidence == tuple(
        ref for ref in row.evidence_refs if ref.dimension is dimension
    )
    assert finding.rule_version == result.rule_version
    assert finding.rule_fingerprint == result.rule_fingerprint
    assert comment.text[finding.hits[0].start : finding.hits[0].end] == comment.text
    assert data.records[0].score(dimension).value == 2
    assert row.scores[dimension] == 2 and comment.text.endswith("5分")


@pytest.mark.parametrize(
    "text",
    [
        "算法与数据结构评分为3分",
        "本次算法与数据结构得分为3分",
        "算法与数据结构评分：3分",
        "  算法与数据结构评分为3分。 ",
    ],
)
def test_matching_explicit_score_does_not_claim_global_consistency(
    make_case: CaseFactory,
    text: str,
) -> None:
    row, comment = make_case(text)
    result = review_comments(
        prepare_dataset([row], policy=ScorePolicy()), [comment], batch_id=row.batch_id
    )
    assert result.records[0].state is ReviewState.NO_EXPLICIT_CONFLICT
    assert result.records[0].findings == ()


@pytest.mark.parametrize(
    "text",
    [
        "算法基础扎实",
        "算法基础薄弱",
        "算法与数据结构评分不是5分",
        "例如，算法与数据结构评分为5分",
        "上次算法与数据结构评分为5分",
        "小组示例：算法与数据结构评分为5分",
        "算法与数据结构评分为5分？",
        "算法与数据结构评分为5分?",
        "算法与数据结构评分为5分；请继续练习",
        "另一位同学，算法与数据结构评分为5分",
        "“算法与数据结构评分为5分”",
        "若算法与数据结构评分为5分",
    ],
)
def test_semantic_difference_and_ambiguous_claims_are_not_score_conflicts(
    make_case: CaseFactory,
    text: str,
) -> None:
    row, comment = make_case(text, score=1)
    review = review_comments(
        prepare_dataset([row], policy=ScorePolicy()), [comment], batch_id=row.batch_id
    ).records[0]
    assert review.state is ReviewState.MANUAL
    assert all(finding.kind is FindingKind.MANUAL for finding in review.findings)


@pytest.mark.parametrize(
    "score,text,reason,options",
    [
        (None, "算法与数据结构评分为3分", "recorded_score_missing_or_invalid", {}),
        ("invalid", "算法与数据结构评分为3分", "recorded_score_missing_or_invalid", {}),
        (3, "算法与数据结构评分为6分", "stated_score_outside_policy", {}),
        (3, "算法与数据结构评分为5分", "score_evidence_missing", {"score_refs": False}),
        (
            3,
            "算法与数据结构评分为5分",
            "comment_dimension_mismatch",
            {"comment_dimension": Dimension.STRATEGY},
        ),
        (
            3,
            "算法与数据结构评分为3分；算法与数据结构评分为5分",
            "multiple_different_stated_scores",
            {},
        ),
    ],
)
def test_unreliable_score_comparisons_are_manual(
    make_case: CaseFactory,
    score: object,
    text: str,
    reason: str,
    options: dict[str, object],
) -> None:
    row, comment = make_case(text, score=score, **options)
    review = review_comments(
        prepare_dataset([row], policy=ScorePolicy()), [comment], batch_id=row.batch_id
    ).records[0]
    assert review.state is ReviewState.MANUAL
    assert review.findings[0].reason_code == reason


def test_half_scores_and_multiple_dimension_assertions(make_case: CaseFactory) -> None:
    row, comment = make_case("算法与数据结构评分为2.5分；代码实现能力评分为5分", score=2.5)
    review = review_comments(
        prepare_dataset([row], policy=ScorePolicy()), [comment], batch_id=row.batch_id
    ).records[0]
    assert len(review.findings) == 1
    assert review.findings[0].dimension is Dimension.IMPLEMENTATION
    assert (review.findings[0].recorded_score, review.findings[0].stated_score) == (3, 5)


def test_missing_comment_for_a_scoped_record_is_manual(make_case: CaseFactory) -> None:
    row, _ = make_case("synthetic")
    review = review_comments(
        prepare_dataset([row], policy=ScorePolicy()), [], batch_id=row.batch_id
    ).records[0]
    assert review.state is ReviewState.MANUAL
    assert review.findings[0].rule_id == "review.missing_comment"
    assert review.findings[0].comments == ()


def test_invalid_stated_step_is_not_rounded(make_case: CaseFactory) -> None:
    from fractions import Fraction

    row, comment = make_case("算法与数据结构评分为2.25分")
    review = review_comments(
        prepare_dataset([row], policy=ScorePolicy(step=Fraction(1, 2))),
        [comment],
        batch_id=row.batch_id,
    ).records[0]
    assert review.state is ReviewState.MANUAL
    assert review.findings[0].reason_code == "stated_score_outside_policy"
