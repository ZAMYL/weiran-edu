from collections.abc import Callable
from dataclasses import replace

from app.analysis.edu import AssessmentRecord, ScorePolicy, prepare_dataset
from app.analysis.edu.comments import (
    DEFAULT_RULES,
    CommentInput,
    FindingKind,
    ReviewState,
    review_comments,
)

CaseFactory = Callable[..., tuple[AssessmentRecord, CommentInput]]


def test_same_comment_different_scores_is_duplicate_not_automatic_error(
    make_case: CaseFactory,
) -> None:
    cases = [
        make_case("算法基础扎实", student=f"synthetic-{i}", score=score)
        for i, score in enumerate((1, 3, 5))
    ]
    report = review_comments(
        prepare_dataset([row for row, _ in cases], policy=ScorePolicy()),
        [comment for _, comment in cases],
        batch_id="synthetic-batch",
    )
    assert len(report.duplicate_groups) == 1
    group = report.duplicate_groups[0]
    assert (group.occurrences, group.distinct_assessments) == (3, 3)
    assert group.comments == tuple(item.classifications[0].comment for item in report.records)
    assert all(record.state is ReviewState.MANUAL for record in report.records)
    assert all(
        finding.kind is not FindingKind.SCORE_CONFLICT
        for record in report.records
        for finding in record.findings
    )
    assert all(
        any(finding.kind is FindingKind.DUPLICATE for finding in record.findings)
        for record in report.records
    )


def test_repeated_explicit_assertion_is_compared_to_each_record_separately(
    make_case: CaseFactory,
) -> None:
    cases = [
        make_case("算法与数据结构评分为5分", student=f"synthetic-{i}", score=score)
        for i, score in enumerate((5, 2))
    ]
    report = review_comments(
        prepare_dataset([row for row, _ in cases], policy=ScorePolicy()),
        [comment for _, comment in cases],
        batch_id="synthetic-batch",
    )
    assert [review.state for review in report.records] == [
        ReviewState.MANUAL,
        ReviewState.CANDIDATE,
    ]
    conflicts = [
        finding
        for review in report.records
        for finding in review.findings
        if finding.kind is FindingKind.SCORE_CONFLICT
    ]
    assert len(conflicts) == 1 and conflicts[0].assessments[0].student_id == "synthetic-1"
    assert report.duplicate_groups[0].occurrences == 2


def test_empty_text_is_not_a_repeated_comment(make_case: CaseFactory) -> None:
    cases = [make_case(text, student=f"synthetic-{i}") for i, text in enumerate((None, "", " "))]
    report = review_comments(
        prepare_dataset([row for row, _ in cases], policy=ScorePolicy()),
        [comment for _, comment in cases],
        batch_id="synthetic-batch",
    )
    assert report.duplicate_groups == ()
    assert all(review.findings[0].reason_code == "empty_comment" for review in report.records)


def test_duplicate_whitespace_policy_is_explicit_and_preserves_raw_text(
    make_case: CaseFactory,
) -> None:
    cases = [
        make_case(text, student=f"synthetic-{i}")
        for i, text in enumerate(("算法基础扎实", " 算法基础扎实 "))
    ]
    data = prepare_dataset([row for row, _ in cases], policy=ScorePolicy())
    comments = [comment for _, comment in cases]
    assert review_comments(data, comments, batch_id="synthetic-batch").duplicate_groups == ()
    trimmed = replace(DEFAULT_RULES, version="synthetic-trim-v1", trim_duplicate_whitespace=True)
    report = review_comments(data, comments, batch_id="synthetic-batch", rules=trimmed)
    assert report.duplicate_groups[0].occurrences == 2
    assert comments[1].text == " 算法基础扎实 "


def test_same_record_two_comment_cells_count_as_two_occurrences_one_assessment(
    make_case: CaseFactory,
) -> None:
    row, comment = make_case("算法基础扎实")
    extra = replace(comment, comment_id="synthetic-extra", evidence=row.evidence_refs[-1])
    report = review_comments(
        prepare_dataset([row], policy=ScorePolicy()), [comment, extra], batch_id=row.batch_id
    )
    group = report.duplicate_groups[0]
    assert (group.occurrences, group.distinct_assessments) == (2, 1)


def test_batch_and_cohort_isolation_and_stable_order(make_case: CaseFactory) -> None:
    first, c1 = make_case("算法基础扎实", student="synthetic-001")
    second, c2 = make_case("算法基础扎实", student="synthetic-002")
    other, c3 = make_case("算法基础扎实", student="synthetic-001", batch="other-batch")
    data = prepare_dataset([other, second, first], policy=ScorePolicy())
    forward = review_comments(data, [c1, c2, c3], batch_id=first.batch_id)
    backward = review_comments(data, [c3, c2, c1], batch_id=first.batch_id)
    assert forward == backward
    assert forward.duplicate_groups[0].occurrences == 2
    assert all(review.assessment.batch_id == first.batch_id for review in forward.records)
    selected = review_comments(
        data, [c1, c2, c3], batch_id=first.batch_id, student_ids=["synthetic-001", "synthetic-001"]
    )
    assert len(selected.records) == 1 and selected.duplicate_groups == ()
    assert (
        review_comments(data, [c1, c2, c3], batch_id=first.batch_id, student_ids=[]).records == ()
    )
    assert review_comments(data, [c1, c2, c3], batch_id="empty-batch").records == ()
