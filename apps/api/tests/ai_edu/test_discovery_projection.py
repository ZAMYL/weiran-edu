"""Model projection is a separate positive schema, never a trimmed teacher card."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from app.ai.edu.discovery import Limits, run_agent
from app.ai.edu.discovery.mock_http import MockQwenPlanner, adaptive_transport
from app.ai.edu.discovery.models import AgentError
from app.ai.edu.discovery.projection import model_discovery, model_investigation
from app.ai.edu.discovery.synthetic import build
from app.ai.edu.discovery.tools import ToolExecutor, parse_arguments
from app.analysis.edu.discovery import present
from app.analysis.edu.models import Dimension as D

PAIR = {"left": "ALGORITHMS", "right": "IMPLEMENTATION"}
GROUP = {
    "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": "1"}],
    "outcomes": ["IMPLEMENTATION"],
}
BOUNDARY = {
    "conditions": [
        {"dimension": "ALGORITHMS", "operator": "<=", "threshold": "2.75"},
        {"dimension": "STRATEGY", "operator": ">=", "threshold": "4"},
    ],
    "varying_dimension": "ALGORITHMS",
}
CANARY = "synthetic-private-canary-never-send"


def execute(e, name, args=None):
    return e.execute(name, parse_arguments(name, args or {}))


@pytest.mark.parametrize(
    "case,tool,args",
    [
        ("coupled53", "query_dimension_relation", PAIR),
        ("coupled53", "compare_student_groups", GROUP),
        ("rare105", "inspect_rare_combination", PAIR),
        ("boundary30", "verify_threshold_sensitivity", BOUNDARY),
    ],
)
def test_model_discovery_has_positive_fields_and_teacher_display_stays_separate(case, tool, args):
    e = ToolExecutor(build(case), "synthetic-run", Limits())
    with patch.object(e, "_card", side_effect=AssertionError("teacher_card_is_not_model_data")):
        fact = execute(e, tool, args)[0]
        support = execute(e, "inspect_discovery_support", {"fact_id": fact.fact_id})[0]
    model = fact.view["card"]
    assert set(model) == {
        "type",
        "status",
        "rule_version",
        "metrics",
        "conditions",
        "ranking",
        "limitation_codes",
    }
    assert set(model["ranking"]) == {
        "score",
        "support_count",
        "completeness",
        "robustness",
        "is_statistical_confidence",
    }
    assert support.view["qualification"] == "qualified"
    assert "limitations" not in support.view
    teacher = e.finish(
        parse_arguments("finish", {"selected_fact_ids": [fact.fact_id], "reason": "completed"})
    )[0]
    assert teacher["title"] == fact.discovery.title
    assert teacher["summary"] == fact.discovery.summary
    assert teacher["chart"] and teacher["batch_id"] == "synthetic-batch"
    for field in (
        "title",
        "summary",
        "chart",
        "parameters",
        "limitations",
        "controlled_queries",
        "batch_id",
        "snapshot_id",
        "discovery_id",
    ):
        assert field not in model
    assert model["metrics"]["student_denominator"] == teacher["metrics"]["student_denominator"]


def test_internal_free_text_cannot_become_model_facts():
    e = ToolExecutor(build("coupled53"), "synthetic-run", Limits())
    pair, qualified = e.builder.relation(
        D.ALGORITHMS,
        D.IMPLEMENTATION,
    )
    contaminated = replace(qualified, title=CANARY, summary=CANARY, limitations=(CANARY,))
    with patch.object(e.builder, "relation", return_value=(pair, contaminated)):
        fact = execute(e, "query_dimension_relation", PAIR)[0]
    assert CANARY not in json.dumps(fact.view)
    assert model_discovery(contaminated) == model_discovery(qualified)
    support = execute(e, "inspect_discovery_support", {"fact_id": fact.fact_id})[0]
    assert CANARY not in json.dumps(support.view)


def test_future_present_fields_never_enter_actual_native_http_model_messages():
    def poisoned_present(discovery):
        data = present(discovery)
        data["future_private_metadata"] = CANARY
        data["metrics"]["future_private_metric"] = CANARY
        data["ranking"]["future_private_rank"] = CANARY
        data["limitations"] = (CANARY,)
        return data

    async def exercise():
        planner = MockQwenPlanner(adaptive_transport())
        try:
            with patch("app.ai.edu.discovery.tools.present", side_effect=poisoned_present):
                result = await run_agent("coupled53", planner)
            assert result.status == "completed" and len(result.cards) == 1
            assert CANARY not in json.dumps(planner.history)
            assert CANARY not in json.dumps([f.view for f in result.facts])
            # The original aggregate behavior and both sides' actual denominators remain.
            assert result.cards[0]["metrics"]["target_count"] == 26
            assert result.cards[0]["metrics"]["remainder_count"] == 27
            assert result.cards[0]["chart"][0]["difference"]["numerator"] == 4
        finally:
            await planner.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "tool,view",
    [
        (
            "get_dimension_statistics",
            {
                "state": "available",
                "student_denominator": 20,
                "dimensions": [
                    {
                        "dimension": "ALGORITHMS",
                        "state": "available",
                        "valid_count": 20,
                        "mean": {
                            "numerator": 3,
                            "denominator": 1,
                            "display": "3.0000",
                            "private": CANARY,
                        },
                        "private": CANARY,
                    }
                ],
            },
        ),
        ("query_dimension_relation", {"state": "computable", "rho": 1, "paired_count": 20}),
        (
            "compare_student_groups",
            {
                "target_count": 10,
                "remainder_count": 10,
                "differences": [
                    {
                        "dimension": "IMPLEMENTATION",
                        "target_valid": 10,
                        "remainder_valid": 10,
                        "target_mean": {"numerator": 5, "denominator": 1, "private": CANARY},
                        "remainder_mean": {"numerator": 1, "denominator": 1},
                        "difference": {"numerator": 4, "denominator": 1},
                        "private": CANARY,
                    }
                ],
            },
        ),
        ("inspect_rare_combination", {"state": "computed"}),
        (
            "verify_threshold_sensitivity",
            {"strict_count": 10, "inclusive_count": 20, "added_count": 10},
        ),
        (
            "inspect_discovery_support",
            {
                "checked_fact_id": "synthetic-fact",
                "qualification": "qualified",
                "ranking": {
                    "support_count": 20,
                    "score": {"numerator": 1, "denominator": 1, "private": CANARY},
                    "completeness": {"numerator": 1, "denominator": 1},
                    "robustness": {
                        "method": "three_fixed_deletions",
                        "planned": 3,
                        "evaluated": 3,
                        "passed": 3,
                        "value": {"numerator": 1, "denominator": 1, "private": CANARY},
                        "private": CANARY,
                    },
                    "private": CANARY,
                },
            },
        ),
    ],
)
def test_all_investigation_shapes_drop_unreviewed_fields_at_every_depth(tool, view):
    value = {
        **view,
        "private": CANARY,
        "card": {"private": CANARY},
        "title": CANARY,
        "summary": CANARY,
        "evidence_ids": [CANARY],
    }
    projected = model_investigation(tool, value)
    assert CANARY not in json.dumps(projected)
    assert "card" not in projected and "title" not in projected


def test_unknown_projection_category_fails_closed():
    with pytest.raises(AgentError, match="unsupported_model_projection"):
        model_investigation("unknown", {})
