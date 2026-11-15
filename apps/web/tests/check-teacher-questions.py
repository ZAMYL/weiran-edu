"""Check frontend suggestion fixtures against the actual unchanged task goal parser."""

import json
from pathlib import Path

from app.ai.edu.discovery.models import AgentError
from app.importing.edu.types import DIMENSIONS

from prototypes.research_agent_v2.goals import understand
from prototypes.teacher_agent_bridge.provider import ALIASES


def main() -> None:
    cases = json.loads(
        (Path(__file__).parent / "fixtures/teacher-questions.synthetic.json").read_text(
            encoding="utf-8"
        )
    )
    catalog = {"items": [{"item_id": alias} for alias in ALIASES]}
    for case in cases:
        question = case["question"]
        for alias, dimension in zip(ALIASES, DIMENSIONS, strict=True):
            question = question.replace(dimension.label, alias)
        assert understand(question, catalog).kind == case["goal"], case["id"]
    unknown = (
        "有什么问题",
        "帮我分析一下",
        "为什么学生学不好",
        "优先核查并修改评分",
        "有哪些值得关注的问题并给我学生姓名",
    )
    for question in unknown:
        try:
            understand(question, catalog)
        except AgentError:
            pass
        else:
            raise AssertionError("Unknown/unsafe input was admitted")
    print(
        json.dumps(
            {
                "supported_suggestions": len(cases),
                "unknown_or_unsafe_rejected": len(unknown),
                "real_qwen": "not_run",
            }
        )
    )


if __name__ == "__main__":
    main()
