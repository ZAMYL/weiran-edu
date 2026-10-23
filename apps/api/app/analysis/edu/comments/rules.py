from dataclasses import dataclass
from hashlib import sha256
from json import dumps

from ..models import AnalysisInputError, Dimension, require_id
from .models import Context, ContextRule, NumericRule, TopicRule


def _literals(values: tuple[str, ...]) -> None:
    if (
        not isinstance(values, tuple)
        or not values
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise AnalysisInputError("nonempty_literal_tuple_required")
    if len(set(values)) != len(values):
        raise AnalysisInputError("duplicate_literal")


@dataclass(frozen=True)
class RuleSet:
    version: str
    basis_ref: str
    topics: tuple[TopicRule, ...]
    contexts: tuple[ContextRule, ...]
    numeric_rules: tuple[NumericRule, ...]
    guard_terms: tuple[str, ...]
    trim_duplicate_whitespace: bool = False
    max_comment_chars: int = 4000

    def __post_init__(self) -> None:
        require_id(self.version)
        require_id(self.basis_ref)
        if not isinstance(self.topics, tuple) or not self.topics:
            raise AnalysisInputError("topic_rules_required")
        if not isinstance(self.contexts, tuple) or not isinstance(self.numeric_rules, tuple):
            raise AnalysisInputError("immutable_rule_tuples_required")
        if not isinstance(self.trim_duplicate_whitespace, bool) or (
            isinstance(self.max_comment_chars, bool)
            or not isinstance(self.max_comment_chars, int)
            or self.max_comment_chars < 1
        ):
            raise AnalysisInputError("invalid_comment_options")
        _literals(self.guard_terms)
        identifiers = set()
        dimensions = set()
        terms = {}
        for rule in self.topics:
            if not isinstance(rule, TopicRule) or not isinstance(rule.dimension, Dimension):
                raise AnalysisInputError("typed_topic_rule_required")
            require_id(rule.title)
            _literals(rule.terms)
            if rule.dimension in dimensions:
                raise AnalysisInputError("duplicate_topic_dimension")
            dimensions.add(rule.dimension)
            terms[rule.dimension] = rule.terms
        labels = set()
        for rule in (*self.topics, *self.contexts, *self.numeric_rules):
            if not isinstance(rule, (TopicRule, ContextRule, NumericRule)):
                raise AnalysisInputError("typed_rule_required")
            require_id(rule.rule_id)
            if rule.rule_id in identifiers:
                raise AnalysisInputError("duplicate_rule_id")
            identifiers.add(rule.rule_id)
            if not isinstance(rule.dimension, Dimension) or rule.dimension not in dimensions:
                raise AnalysisInputError("unknown_rule_dimension")
            if isinstance(rule, ContextRule):
                if rule.context not in {Context.AFFIRMATIVE, Context.NEGATIVE}:
                    raise AnalysisInputError("explicit_context_required")
                _literals(rule.phrases)
                if any(
                    not any(term in phrase for term in terms[rule.dimension])
                    for phrase in rule.phrases
                ):
                    raise AnalysisInputError("context_must_include_its_topic_anchor")
            if isinstance(rule, NumericRule):
                require_id(rule.label)
                if rule.label in labels:
                    raise AnalysisInputError("ambiguous_numeric_label")
                labels.add(rule.label)

    @property
    def status(self) -> str:
        return "draft"

    @property
    def fingerprint(self) -> str:
        payload = {
            "version": self.version,
            "basis_ref": self.basis_ref,
            "status": self.status,
            "topics": [(r.rule_id, r.dimension.name, r.title, r.terms) for r in self.topics],
            "contexts": [
                (r.rule_id, r.dimension.name, r.context.value, r.phrases) for r in self.contexts
            ],
            "numeric_rules": [(r.rule_id, r.dimension.name, r.label) for r in self.numeric_rules],
            "guard_terms": self.guard_terms,
            "trim_duplicates": self.trim_duplicate_whitespace,
            "max_comment_chars": self.max_comment_chars,
        }
        return sha256(dumps(payload, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


DEFAULT_RULES = RuleSet(
    version="edu-comment-0.1.0",
    basis_ref="docs/ANALYSIS_RULES.md#五个维度",
    topics=(
        TopicRule(
            "topic.algorithms", Dimension.ALGORITHMS, "算法与数据结构基础", ("算法", "数据结构")
        ),
        TopicRule(
            "topic.implementation",
            Dimension.IMPLEMENTATION,
            "代码实现、调试与边界处理",
            ("代码", "调试", "边界条件", "边界处理"),
        ),
        TopicRule(
            "topic.modeling", Dimension.MODELING, "问题分析与建模", ("问题分析", "问题建模", "建模")
        ),
        TopicRule(
            "topic.efficiency",
            Dimension.EFFICIENCY,
            "时间和空间复杂度",
            ("时间复杂度", "空间复杂度", "复杂度", "效率意识"),
        ),
        TopicRule(
            "topic.strategy",
            Dimension.STRATEGY,
            "解题策略与优化",
            ("解题策略", "策略选择", "优化思路", "解题方法"),
        ),
    ),
    contexts=(
        ContextRule(
            "context.algorithms.affirmative",
            Dimension.ALGORITHMS,
            Context.AFFIRMATIVE,
            ("算法基础扎实", "数据结构运用正确"),
        ),
        ContextRule(
            "context.algorithms.negative",
            Dimension.ALGORITHMS,
            Context.NEGATIVE,
            ("算法基础薄弱", "算法基础不扎实", "数据结构运用不当"),
        ),
        ContextRule(
            "context.implementation.affirmative",
            Dimension.IMPLEMENTATION,
            Context.AFFIRMATIVE,
            ("代码实现正确", "边界处理完善", "调试方法合理"),
        ),
        ContextRule(
            "context.implementation.negative",
            Dimension.IMPLEMENTATION,
            Context.NEGATIVE,
            ("代码实现有误", "调试存在困难", "未考虑边界条件"),
        ),
        ContextRule(
            "context.modeling.affirmative",
            Dimension.MODELING,
            Context.AFFIRMATIVE,
            ("建模准确", "问题分析清晰"),
        ),
        ContextRule(
            "context.modeling.negative",
            Dimension.MODELING,
            Context.NEGATIVE,
            ("建模不准确", "问题分析不清晰"),
        ),
        ContextRule(
            "context.efficiency.affirmative",
            Dimension.EFFICIENCY,
            Context.AFFIRMATIVE,
            ("复杂度分析清晰", "时间复杂度分析清晰", "空间复杂度分析清晰", "效率意识良好"),
        ),
        ContextRule(
            "context.efficiency.negative",
            Dimension.EFFICIENCY,
            Context.NEGATIVE,
            ("复杂度分析不清晰", "时间复杂度分析不清晰", "空间复杂度分析不清晰", "效率意识不足"),
        ),
        ContextRule(
            "context.strategy.affirmative",
            Dimension.STRATEGY,
            Context.AFFIRMATIVE,
            ("解题策略合理", "优化思路清晰"),
        ),
        ContextRule(
            "context.strategy.negative",
            Dimension.STRATEGY,
            Context.NEGATIVE,
            ("解题策略不合理", "优化思路不清晰"),
        ),
    ),
    numeric_rules=tuple(
        NumericRule(f"claim.{dimension.name.lower()}", dimension, dimension.value)
        for dimension in Dimension
    ),
    guard_terms=(
        "例如",
        "比如",
        "举例",
        "反例",
        "示例",
        "样例",
        "假设",
        "如果",
        "若",
        "可能",
        "也许",
        "似乎",
        "听说",
        "据说",
        "并非",
        "不是",
        "不能",
        "不认为",
        "没有",
        "未发现",
        "未出现",
        "并不",
        "不一定",
        "未必",
        "不差",
        "不弱",
        "不薄弱",
        "不缺",
        "不乏",
        "不但",
        "不仅",
        "不只是",
        "不正确",
        "不成立",
        "说法",
        "建议",
        "应该",
        "希望",
        "目标",
        "其他同学",
        "其他人",
        "另一位",
        "上次",
        "此前",
        "曾经",
        "去年",
        "之前",
        "过去",
        "历史",
        "对比",
        "比较",
        "模板",
        "引用",
        "原文",
        "标准",
        "满分",
        '"',
        "'",
        "“",
        "”",
        "‘",
        "’",
        "「",
        "」",
        "『",
        "』",
        "？",
        "?",
    ),
)
