"""P0-C: unsupported intents must not hide behind valid goal keywords."""

import pytest
from prototypes.research_agent_v2.goals import understand

from app.ai.edu.discovery.models import AgentError


@pytest.mark.parametrize(
    "question",
    [
        "优先核查所有学生的姓名和学号",
        "核查I01缺分并列出学生姓名",
        "核查并把I01的分数改为5分",
        "调查I01与I02相关并调整评分",
        "证明I01导致I02",
        "确认I01与I02存在因果关系",
        "优先帮我写一首诗",
        "核查缺分并查看别的批次",
    ],
)
def test_prohibited_or_unknown_requests_cannot_become_supported_goals(question):
    catalog = {"items": [{"item_id": "I01"}, {"item_id": "I02"}]}
    with pytest.raises(AgentError):
        understand(question, catalog)


def test_existing_scoped_priority_command_is_preserved():
    catalog = {"items": [{"item_id": "I01"}, {"item_id": "I02"}]}
    goal = understand("优先核查 I01、I02", catalog)
    assert goal.kind == "priority" and goal.items == ("I01", "I02")


@pytest.mark.parametrize(
    "question",
    [
        "prove I01 causes I02",
        "inspect missing I01 and list student names",
        "analyze relation I01 I02 and modify scores",
    ],
)
def test_english_prohibited_intents_are_not_hidden_by_valid_goal_keywords(question):
    catalog = {"items": [{"item_id": "I01"}, {"item_id": "I02"}]}
    with pytest.raises(AgentError):
        understand(question, catalog)


@pytest.mark.parametrize(
    "question,kind,items",
    [
        ("优先核查 I01、I02", "priority", ("I01", "I02")),
        ("优先核查评分异常 I01，I02。", "priority", ("I01", "I02")),
        ("核查 I01 缺分", "quality", ("I01",)),
        ("核查缺分 I01 I02", "quality", ("I01", "I02")),
        ("核查所有项目的缺分", "quality", ("I01", "I02", "I03")),
        ("核查缺分和有效样本", "quality", ("I01", "I02", "I03")),
        ("inspect missing I01", "quality", ("I01",)),
        ("调查 I01 和 I02 相关", "relation", ("I01", "I02")),
        ("比较 I01 与 I02 的关联。", "relation", ("I01", "I02")),
        ("关联 I01 I02", "relation", ("I01", "I02")),
        ("Compare I01 and I02 correlation?", "relation", ("I01", "I02")),
        ("analyze relation I01 I02", "relation", ("I01", "I02")),
        ("比较 I01 和 I02 的群体差异", "group", ("I01", "I02")),
        ("比较 I01、I02 的组合与 I03 的群体差异", "group", ("I01", "I02", "I03")),
        ("比较 I01、I02 与 I03 的群体差异", "group", ("I01", "I02", "I03")),
        ("群体差异 I01 I02 I03", "group", ("I01", "I02", "I03")),
        ("为什么 I01 和 I02 相关", "explanation", ("I01", "I02")),
        ("为什么相关 I01 I02", "explanation", ("I01", "I02")),
        ("why are I01 and I02 correlated?", "explanation", ("I01", "I02")),
    ],
)
def test_complete_controlled_forms_and_persisted_task_labels(question, kind, items):
    catalog = {"items": [{"item_id": item} for item in ("I01", "I02", "I03")]}
    goal = understand(question, catalog)
    assert (goal.kind, goal.items) == (kind, items)


@pytest.mark.parametrize(
    "question",
    [
        "比较 I01 与 I02 的关联，并列出每位学生的名字",
        "Compare I01 and I02 correlation and list each student's full name",
        "Compare I01 and I02 correlation in another batch",
        "优先核查 I01、I02，再画一只猫",
        "核查 I01 缺分，再画一只猫",
        "调查 I01 和 I02 相关，再画一只猫",
        "比较 I01 和 I02 的群体差异，再画一只猫",
        "为什么 I01 和 I02 相关，再画一只猫",
        "优先核查 I01 并核查 I02 缺分",
        "比较 I01 与 I02 的关联；核查 I01 缺分",
        "比较 I01 与 I02 的关联，读取隔壁班的数据",
        "Compare I01 and I02 correlation using a different assessment",
        "Compare I01 and I02 correlation and show each participant's legal name",
        "比较 I01 和 I02 的群体差异，展示每个人的个人资料",
        "优先核查 I01 然后展示每个人的全名",
        "为什么 I01 和 I02 相关，然后获取所有人的身份",
        "correlation pineapple I01 I02",
        "relation I01 I02 missing",
        "有哪些值得关注的问题？优先核查 I01",
        "I01优先核查I02",
        "优先核查 I01 I02 arbitrary",
        "优先核查 i01",
        "调查I01000和I02的关联",
        "调查 I01 和 I99 相关",
        "调查 I01I02 相关",
        "比较 I01 I02 I03 I04 的群体差异",
        "为什么 I01 和 I02 相关? list identities",
    ],
)
def test_no_unmatched_prefix_suffix_identity_batch_or_second_intent(question):
    catalog = {"items": [{"item_id": f"I{index:02d}"} for index in range(1, 5)]}
    with pytest.raises(AgentError):
        understand(question, catalog)
