"""Conservative Chinese/English goal mapping, not unconstrained intent execution."""

import re
from typing import Literal

from app.ai.edu.discovery.models import AgentError, Strict
from pydantic import Field

# Whole-question aliases only: "关注" alone must not admit arbitrary requests.
_PRIORITY_QUESTIONS = frozenset(
    {
        "有哪些值得关注的问题",
        "有什么值得关注的问题",
        "哪些问题值得关注",
        "有哪些需要关注的问题",
        "有什么需要关注的问题",
        "哪些问题需要关注",
    }
)

# Preserve current canonical commands, but a keyword alone is not a supported request.
_PRIORITY_COMMANDS = frozenset(
    {"优先核查", "优先核查评分异常", "核查评分异常", "识别值得优先核查的评分异常"}
)

# A goal must account for the entire request, including every item and connector.
# Keep canonical persisted task labels as well as complete teacher question forms.
# Case-insensitive English verbs must not turn lowercase/embedded text into item IDs.
_ITEM = r"(?<![A-Za-z0-9_])(?-i:[IQ][0-9]{2,3})(?![A-Za-z0-9_])"
_SEPARATOR = r"(?:\s*(?:、|，|,|和|与|及|&|\band\b)\s*|\s+)"
_PAIR = rf"{_ITEM}{_SEPARATOR}{_ITEM}"
_ITEMS = rf"{_ITEM}(?:{_SEPARATOR}{_ITEM})*"
_PRIORITY = "(?:" + "|".join(re.escape(c) for c in sorted(_PRIORITY_COMMANDS)) + ")"
_QUALITY = r"(?:缺分和有效样本|缺分|缺失|有效样本|样本|证据不足)"
_RELATION = r"(?:关联|相关性?|高度相关)"
_GOAL_PATTERNS = {
    "priority": (rf"{_PRIORITY}(?:\s*{_ITEMS})?",),
    "relation": (
        rf"(?:调查|比较|分析)\s*{_PAIR}\s*(?:的\s*)?{_RELATION}",
        rf"(?:关联|相关性?|relation|correlation)\s+{_PAIR}",
        rf"(?:compare|investigate|analy[sz]e)\s+{_PAIR}\s+(?:relation|correlation)",
        rf"(?:investigate|analy[sz]e)\s+(?:relation|correlation)\s+{_PAIR}",
    ),
    "group": (
        rf"比较\s*{_ITEMS}\s*(?:的\s*)?群体差异",
        rf"比较\s*{_PAIR}\s*(?:的\s*)?组合\s*(?:与|和)\s*{_ITEM}\s*(?:的\s*)?群体差异",
        rf"群体差异\s+{_ITEMS}",
        rf"compare\s+{_ITEMS}\s+group\s+differences?",
        rf"compare\s+group\s+differences?\s+{_ITEMS}",
    ),
    "quality": (
        rf"(?:核查|检查|分析)\s*(?:(?:所有项目|{_ITEMS})\s*(?:的\s*)?)?{_QUALITY}",
        rf"核查{_QUALITY}\s+{_ITEMS}",
        rf"(?:inspect|check)\s+missing(?:\s+{_ITEMS})?",
        rf"(?:inspect|check)\s+{_ITEMS}\s+missing",
    ),
    "explanation": (
        rf"为什么\s*{_PAIR}\s*(?:的\s*)?{_RELATION}",
        rf"为什么相关\s+{_PAIR}",
        rf"why\s+(?:are\s+)?{_PAIR}\s+(?:correlated|related)",
        rf"explain\s+(?:the\s+)?(?:correlation|relation)\s+(?:between\s+)?{_PAIR}",
    ),
}


class Goal(Strict):
    kind: Literal["priority", "relation", "group", "quality", "explanation"]
    items: tuple[str, ...] = Field(max_length=64)

    def view(self) -> dict:
        return {
            "type": self.kind,
            "items": self.items,
            "rubric_evidence": "unavailable",
            "task_evidence": "unavailable",
            "no_causal_conclusions": True,
        }


def understand(question: str, catalog: dict) -> Goal:
    if not isinstance(question, str) or not 1 <= len(question) <= 300:
        raise AgentError("unsupported_goal")
    if re.search(r"忽略|ignore|SQL|https?://|[/\\]|改分|修改成绩|其他批次", question, re.I):
        raise AgentError("unsafe_or_unsupported_goal")
    if re.search(
        r"姓名|学号|真实身份|身份证|手机号|联系方式|学生名单|别的批次|其它批次|另一批次"
        r"|其他老师|导致|因果|造成|引起|\bcaus(?:es?|ed|al|ation)\b"
        r"|\bstudent\s+(?:names?|ids?|numbers?)\b",
        question,
        re.I,
    ) or (
        re.search(
            r"修改|调整|更改|删除|覆盖|更新|重置|改为|改成|设为|调高|调低"
            r"|\b(?:modify|adjust|change|delete|overwrite|update|reset)\b",
            question,
            re.I,
        )
        and re.search(r"评分|分数|成绩|\b(?:scores?|grades?|marks?)\b", question, re.I)
    ):
        raise AgentError("unsafe_or_unsupported_goal")
    items = tuple(
        dict.fromkeys(re.findall(r"(?<![A-Za-z0-9_])[IQ]\d{2,3}(?![A-Za-z0-9_])", question))
    )
    allowed = {i["item_id"] for i in catalog["items"]}
    if not set(items) <= allowed:
        raise AgentError("goal_item_outside_scope")
    request = question.strip().rstrip("？?。.!！").strip()
    if request in _PRIORITY_QUESTIONS:
        kind = "priority"
    else:
        kind = next(
            (
                kind
                for kind, patterns in _GOAL_PATTERNS.items()
                if any(re.fullmatch(pattern, request, re.I) for pattern in patterns)
            ),
            None,
        )
        if kind is None:
            raise AgentError("unsupported_goal")
    if kind in ("relation", "explanation") and len(items) != 2:
        raise AgentError("goal_needs_two_items")
    if kind == "group" and not 2 <= len(items) <= 3:
        raise AgentError("goal_needs_condition_and_outcome_items")
    if kind == "quality" and not items:
        items = tuple(i["item_id"] for i in catalog["items"])
    return Goal(kind=kind, items=items)


def reflect(goal: Goal, executor) -> dict:
    for fact in executor.facts.values():
        executor._verify(fact)
    observations = list(executor.facts.values())
    matched = []
    for fact in observations:
        v = fact.view.get("metrics", {})
        if goal.kind in ("relation", "explanation"):
            ok = (
                fact.calculation is not None
                and fact.calculation.kind == "item_relation"
                and {v.get("left"), v.get("right")} == set(goal.items)
            )
        elif goal.kind == "group":
            ok = (
                fact.view.get("type") == "item_group_difference"
                and v.get("outcome") == goal.items[-1]
                and {p["item_id"] for p in v["conditions"]} == set(goal.items[:-1])
            )
        elif goal.kind == "quality":
            covered = {
                i["item_id"]
                for i in fact.view.get("items", [])
                if i.get("state") == "available" and "states" in i
            }
            ok = set(goal.items) <= covered or (
                fact.view.get("type") == "item_quality_review"
                and len(goal.items) == 1
                and v.get("item_id") == goal.items[0]
            )
        else:
            involved = {v.get("left"), v.get("right"), v.get("item_id"), v.get("outcome")}
            involved.update(p["item_id"] for p in v.get("conditions", []))
            ok = bool(fact.view["qualified"]) and (not goal.items or set(goal.items) <= involved)
        if ok:
            matched.append(fact.fact_id)
    constant_summary = any(
        "items" in f.view
        and f.view["items"]
        and {i["item_id"] for i in f.view["items"]} == set(executor.kernel.items)
        and all(
            i.get("state") == "available"
            and i.get("has_variance") is False
            and set(i.get("states", {})) == {"valid"}
            for i in f.view["items"]
        )
        for f in observations
    )
    state = (
        "no_supported_findings"
        if goal.kind == "priority" and constant_summary
        else "needs_external_evidence"
        if goal.kind == "explanation" and matched
        else "goal_satisfied"
        if matched
        else "needs_investigation"
    )
    return {
        "state": state,
        "matching_fact_ids": matched,
        "evidence_gaps": ["rubric_criteria", "original_task"] if goal.kind == "explanation" else [],
        "observation_is_not_explanation": True,
    }
