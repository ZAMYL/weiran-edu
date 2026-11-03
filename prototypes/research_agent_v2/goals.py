"""Conservative Chinese/English goal mapping, not unconstrained intent execution."""

import re
from typing import Literal

from app.ai.edu.discovery.models import AgentError, Strict
from pydantic import Field


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
    items = tuple(
        dict.fromkeys(re.findall(r"(?<![A-Za-z0-9_])[IQ]\d{2,3}(?![A-Za-z0-9_])", question))
    )
    allowed = {i["item_id"] for i in catalog["items"]}
    if not set(items) <= allowed:
        raise AgentError("goal_item_outside_scope")
    if any(t in question.lower() for t in ("为什么", "原因", "导致", "why", "cause")):
        kind = "explanation"
    elif any(t in question.lower() for t in ("缺分", "缺失", "样本", "missing", "证据不足")):
        kind = "quality"
    elif any(t in question.lower() for t in ("群体", "组合", "差异", "group", "difference")):
        kind = "group"
    elif any(t in question.lower() for t in ("关联", "相关", "relation", "correlation")):
        kind = "relation"
    elif any(t in question for t in ("优先", "异常", "核查")):
        kind = "priority"
    else:
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
