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
