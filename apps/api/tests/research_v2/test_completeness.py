"""Completeness is independent of goal success; no case-specific routing."""

import asyncio
import json
from fractions import Fraction

from prototypes.research_agent_v2.completeness import assess, query_key
from prototypes.research_agent_v2.demo import offline
from prototypes.research_agent_v2.goals import reflect, understand

from app.ai.edu.discovery.models import Limits


def test_early_finish_retains_new_verified_fact_and_reports_partial(tmp_path):
    def omit(chosen, view):
        if any(f.get("type") == "item_relation" for f in view["facts"]):
            return {
                "name": "finish",
                "arguments": {"selected_fact_ids": [], "reason": "no_valid_findings"},
            }
        return chosen

    result, executor = asyncio.run(
        offline("research-v2-final-relation", "识别值得优先核查的评分异常", tmp_path, fault=omit)
    )
    assert result["observation_goal"]["state"] == "goal_satisfied"
    assert result["stop_reason"] == "stopped_with_uncovered_directions"
    assert result["investigation_status"] == "partial"
    assert result["investigation_coverage"]["can_continue"]
    assert result["findings"][0]["type"] == "item_relation"
    assert len(executor.facts) == 2 and result["model_selected_fact_ids"] == ()


def test_budget_stops_without_claiming_full_coverage(tmp_path):
    result, _ = asyncio.run(
        offline(
            "research-v2-final-relation",
            "识别值得优先核查的评分异常",
            tmp_path,
            limits=Limits(tool_attempts=2),
        )
    )
    coverage = result["investigation_coverage"]
    assert result["stop_reason"] == "budget_limited"
    assert result["tool_attempts"] == 2 and result["model_requests"] == 2
    assert coverage["uncovered_direction_ids"] and not coverage["can_continue"]
    assert not coverage["global_investigation_exhaustive"]


def test_coverage_proposal_does_not_execute_or_issue_facts(tmp_path):
    result, executor = asyncio.run(
        offline(
            "research-v2-final-relation",
            "识别值得优先核查的评分异常",
            tmp_path,
            fault=lambda chosen, view: {
                "name": "finish",
                "arguments": {"selected_fact_ids": [], "reason": "no_valid_findings"},
            },
        )
    )
    goal = understand("识别值得优先核查的评分异常", executor.kernel.catalog())
    count, released, attempts = (
        len(executor.facts),
        list(executor.released),
        len(executor.attempt_ledger),
    )
    coverage = assess(goal, executor, tools_remaining=4, models_remaining=5)
    assert coverage["directions"] and len(coverage["directions"]) <= 4
    assert (len(executor.facts), executor.released, len(executor.attempt_ledger)) == (
        count,
        released,
        attempts,
    )
    assert count == 1
    assert reflect(goal, executor)["state"] == "needs_investigation"


def test_query_semantics_do_not_collapse_different_outcomes():
    a = {
        "conditions": [{"item_id": "I01", "operator": "<=", "threshold": "1.00"}],
        "outcomes": ["I02"],
    }
    b = {
        "conditions": [{"item_id": "I01", "operator": "<=", "threshold": "1"}],
        "outcomes": ["I02"],
    }
    c = {**b, "outcomes": ["I03"]}
    assert query_key("compare_student_groups", a) == query_key("compare_student_groups", b)
    assert query_key("compare_student_groups", b) != query_key("compare_student_groups", c)


def test_same_goal_different_data_changes_investigation_without_oracle(tmp_path):
    async def run():
        return [
            await offline(recipe, "识别值得优先核查的评分异常", tmp_path / str(i))
            for i, recipe in enumerate(
                ("research-v2-final-interaction", "research-v2-final-relation")
            )
        ]

    (left, _), (right, _) = asyncio.run(run())
    left_query = next(
        t["arguments"] for t in left["tool_ledger"] if t["tool"] == "compare_student_groups"
    )
    right_query = next(
        t["arguments"] for t in right["tool_ledger"] if t["tool"] == "compare_student_groups"
    )
    assert len(left_query["conditions"]) == 2 and len(right_query["conditions"]) == 1
    assert left["stop_reason"] == right["stop_reason"] == "bounded_scope_stop"
    for result in (left, right):
        assert result["investigation_coverage"]["uninvestigated_pairs"]["count"] > 0
        for request in result["native_requests"]:
            payload = json.dumps(request)
            assert (
                '"case_id"' not in payload
                and '"expected"' not in payload
                and '"oracle"' not in payload
            )


def test_reviewer_input_scale_and_witness_binding_are_preserved():
    from prototypes.adaptive_assessment.analysis import Kernel
    from prototypes.adaptive_assessment.synthetic import admit

    source = admit("research-v21-review-decimal")
    source.validate()
    assert source.envelope.data.items[0].scale.step == "0.5"
    assert source.envelope.data.items[0].scale.maximum == "10"
    kernel = Kernel(source.envelope)
    actual, _ = kernel.statistics("Q03")
    assert actual["mean"] == Fraction(25, 4)


def test_teacher_and_technical_projection_include_boundaries(tmp_path):
    from prototypes.research_agent_v2.runtime import markdown, technical_markdown

    result, _ = asyncio.run(
        offline("research-v2-capability", "比较I01、I02的组合与I03的群体差异", tmp_path)
    )
    teacher, technical = markdown(result), technical_markdown(result)
    assert "Spearman=0" in teacher and "I01 <= 1" in teacher and "I02 <= 1" in teacher
    assert "12人" in teacher and "36人" in teacher and "不是因果证明" in teacher
    for key in ("stages", "native_requests", "trace", "budget", "investigation_coverage"):
        assert "## " + key in technical
    assert result["stages"][1]["arguments"]["left"] == "I01"
    assert result["stages"][-1]["tool_attempts_used"] == result["tool_attempts"]
