from collections.abc import Callable

import pytest

from app.analysis.edu import AssessmentRecord, Dimension
from app.analysis.edu.comments import (
    ClassificationState,
    CommentInput,
    Context,
    classify_comment,
)

CaseFactory = Callable[..., tuple[AssessmentRecord, CommentInput]]


@pytest.mark.parametrize(
    "text,dimension,context",
    [
        ("算法基础扎实", Dimension.ALGORITHMS, Context.AFFIRMATIVE),
        ("算法基础薄弱", Dimension.ALGORITHMS, Context.NEGATIVE),
        ("算法基础不扎实", Dimension.ALGORITHMS, Context.NEGATIVE),
        ("代码实现正确", Dimension.IMPLEMENTATION, Context.AFFIRMATIVE),
        ("未考虑边界条件", Dimension.IMPLEMENTATION, Context.NEGATIVE),
        ("问题分析清晰", Dimension.MODELING, Context.AFFIRMATIVE),
        ("建模不准确", Dimension.MODELING, Context.NEGATIVE),
        ("时间复杂度分析清晰", Dimension.EFFICIENCY, Context.AFFIRMATIVE),
        ("复杂度分析不清晰", Dimension.EFFICIENCY, Context.NEGATIVE),
        ("解题策略合理", Dimension.STRATEGY, Context.AFFIRMATIVE),
        ("解题策略不合理", Dimension.STRATEGY, Context.NEGATIVE),
        ("本次涉及数据结构", Dimension.ALGORITHMS, Context.MENTION_ONLY),
    ],
)
def test_single_topic_is_separate_from_context(
    make_case: CaseFactory,
    text: str,
    dimension: Dimension,
    context: Context,
) -> None:
    _, comment = make_case(text)
    result = classify_comment(comment)
    assert result.state is ClassificationState.CLASSIFIED
    assert len(result.topics) == 1
    assert result.topics[0].dimension is dimension
    assert result.topics[0].context is context
    assert result.rule_status == "draft"


def test_multi_topic_does_not_spread_negative_context(make_case: CaseFactory) -> None:
    _, comment = make_case("算法基础扎实，代码实现有误；建模准确")
    result = classify_comment(comment)
    assert [(topic.dimension, topic.context) for topic in result.topics] == [
        (Dimension.ALGORITHMS, Context.AFFIRMATIVE),
        (Dimension.IMPLEMENTATION, Context.NEGATIVE),
        (Dimension.MODELING, Context.AFFIRMATIVE),
    ]


def test_mixed_and_bare_keyword_contexts_do_not_label_a_problem(make_case: CaseFactory) -> None:
    _, comment = make_case("算法基础扎实，但算法基础薄弱")
    assert classify_comment(comment).topics[0].context is Context.MIXED
    _, bare = make_case("讨论算法与数据结构")
    assert classify_comment(bare).topics[0].context is Context.MENTION_ONLY


@pytest.mark.parametrize(
    "text",
    [
        "并非算法基础薄弱",
        "没有算法基础薄弱的问题",
        "反例：算法基础薄弱",
        "如果算法基础薄弱，应复核",
        "曾经算法基础薄弱",
        "引用“算法基础薄弱”",
        "算法基础并不薄弱",
        "算法基础不薄弱",
        "不能说代码实现有误",
        "并非未考虑边界条件",
        "算法基础薄弱的说法不成立",
    ],
)
def test_negation_examples_history_and_quotes_are_uncertain(
    make_case: CaseFactory,
    text: str,
) -> None:
    _, comment = make_case(text)
    result = classify_comment(comment)
    assert result.guard_hits
    assert result.topics
    assert all(topic.context is Context.UNCERTAIN for topic in result.topics)
    assert result.numeric_context_clear is False


@pytest.mark.parametrize("text", ["继续保持学习投入", "实现了目标", "生活效率提高"])
def test_unmatched_text_is_explicitly_unclassified(make_case: CaseFactory, text: str) -> None:
    _, comment = make_case(text)
    result = classify_comment(comment)
    assert result.state is ClassificationState.UNCLASSIFIED
    assert result.topics == ()


@pytest.mark.parametrize("text", [None, "", " \t\n "])
def test_empty_comments_are_explicit(make_case: CaseFactory, text: str | None) -> None:
    _, comment = make_case(text)
    result = classify_comment(comment)
    assert result.state is ClassificationState.EMPTY
    assert result.topics == () and result.claims == ()


def test_offsets_refer_to_the_unchanged_original_text(make_case: CaseFactory) -> None:
    text = "  代码实现正确；\n算法基础扎实  "
    _, comment = make_case(text)
    result = classify_comment(comment)
    hit = next(
        hit
        for topic in result.topics
        for hit in topic.hits
        if hit.rule_id == "context.algorithms.affirmative"
    )
    assert text[hit.start : hit.end] == "算法基础扎实"
    assert comment.text == text
    assert result.comment.assessment == comment.assessment
    assert result.comment.evidence == comment.evidence


@pytest.mark.parametrize(
    "text",
    [
        "排除算法基础薄弱",
        "算法基础薄弱这一结论尚待核实",
        "切勿将算法基础扎实等同于全面掌握",
        "关于算法基础薄弱的描述仍待核实",
        "缺少证据证明算法基础薄弱",
        "老师讨论算法基础薄弱",
    ],
)
def test_unknown_surrounding_context_does_not_become_a_directional_assertion(
    make_case: CaseFactory,
    text: str,
) -> None:
    _, comment = make_case(text)
    assert classify_comment(comment).topics[0].context is Context.UNCERTAIN
