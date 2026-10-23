from collections.abc import Callable
from dataclasses import replace

import pytest

from app.analysis.edu import (
    AnalysisInputError,
    AssessmentRecord,
    Dimension,
    ScorePolicy,
    prepare_dataset,
)
from app.analysis.edu.comments import (
    DEFAULT_RULES,
    CommentInput,
    Context,
    ContextRule,
    classify_comment,
    review_comments,
)

CaseFactory = Callable[..., tuple[AssessmentRecord, CommentInput]]


def test_rules_are_configurable_versioned_and_fingerprinted(make_case: CaseFactory) -> None:
    _, comment = make_case("算法掌握牢固")
    baseline = classify_comment(comment)
    assert baseline.topics[0].context is Context.MENTION_ONLY
    rule = ContextRule(
        "synthetic.context.affirmative",
        Dimension.ALGORITHMS,
        Context.AFFIRMATIVE,
        ("算法掌握牢固",),
    )
    custom = replace(
        DEFAULT_RULES, version="synthetic-config-v2", contexts=(*DEFAULT_RULES.contexts, rule)
    )
    result = classify_comment(comment, rules=custom)
    assert result.topics[0].context is Context.AFFIRMATIVE
    assert result.rule_version == "synthetic-config-v2"
    assert result.rule_fingerprint != baseline.rule_fingerprint
    assert result.rule_fingerprint == custom.fingerprint
    same_version_changed_dictionary = replace(DEFAULT_RULES, contexts=custom.contexts)
    assert same_version_changed_dictionary.fingerprint != DEFAULT_RULES.fingerprint


@pytest.mark.parametrize(
    "changes",
    [
        {"version": ""},
        {"topics": ()},
        {"guard_terms": ("",)},
        {"guard_terms": ("same", "same")},
        {"max_comment_chars": True},
        {"max_comment_chars": 0},
        {"trim_duplicate_whitespace": "yes"},
        {"contexts": []},
    ],
)
def test_invalid_rule_configuration_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(AnalysisInputError):
        replace(DEFAULT_RULES, **changes)


def test_duplicate_ids_and_unanchored_context_rules_are_rejected() -> None:
    with pytest.raises(AnalysisInputError):
        replace(DEFAULT_RULES, contexts=(*DEFAULT_RULES.contexts, DEFAULT_RULES.contexts[0]))
    with pytest.raises(AnalysisInputError):
        replace(
            DEFAULT_RULES,
            contexts=(
                ContextRule(
                    "synthetic-unanchored", Dimension.ALGORITHMS, Context.NEGATIVE, ("薄弱",)
                ),
            ),
        )


def test_unknown_or_tampered_assessment_reference_is_rejected(make_case: CaseFactory) -> None:
    row, comment = make_case("算法基础扎实")
    data = prepare_dataset([row], policy=ScorePolicy())
    for reference in [
        replace(comment.assessment, student_id="synthetic-unknown"),
        replace(comment.assessment, record_id="synthetic-wrong-row"),
    ]:
        with pytest.raises(AnalysisInputError):
            review_comments(data, [replace(comment, assessment=reference)], batch_id=row.batch_id)


def test_duplicate_comment_ids_or_cells_cannot_inflate_frequency(make_case: CaseFactory) -> None:
    row, comment = make_case("算法基础扎实")
    data = prepare_dataset([row], policy=ScorePolicy())
    for extra in [comment, replace(comment, comment_id="synthetic-other-id")]:
        with pytest.raises(AnalysisInputError, match="duplicate_comment_input"):
            review_comments(data, [comment, extra], batch_id=row.batch_id)


def test_comment_evidence_must_be_attached_to_the_assessment(make_case: CaseFactory) -> None:
    row, comment = make_case("算法基础扎实")
    wrong = replace(comment, evidence=row.evidence_refs[0])
    with pytest.raises(AnalysisInputError, match="comment_evidence"):
        classify_comment(wrong)
    unrelated = replace(
        comment, evidence=replace(comment.evidence, location="synthetic-other-cell")
    )
    with pytest.raises(AnalysisInputError, match="comment_evidence"):
        classify_comment(unrelated)


def test_sensitive_marker_is_not_in_results_repr_logs_or_errors(
    make_case: CaseFactory,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "SYNTHETIC_PRIVATE_MARKER_FOR_LOG_TEST"
    row, comment = make_case(f"{marker}；算法与数据结构评分为5分", score=2)
    result = classify_comment(comment)
    report = review_comments(
        prepare_dataset([row], policy=ScorePolicy()), [comment], batch_id=row.batch_id
    )
    assert marker not in repr(comment) + repr(result) + repr(report)
    assert marker not in caplog.text
    output = capsys.readouterr()
    assert output.out == output.err == ""
    with pytest.raises(AnalysisInputError) as failure:
        classify_comment(comment, rules=replace(DEFAULT_RULES, max_comment_chars=4))
    assert marker not in str(failure.value) + repr(failure.value)
    assert comment.text.startswith(marker)
