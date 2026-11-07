"""Review #78 counterexamples; HTTP Mock only, no model-generated expectations."""

import asyncio
import json

import pytest
from prototypes.capability_profiles.agent import ProfileMockPolicy, run_profiles
from prototypes.capability_profiles.demo import teacher_markdown
from prototypes.capability_profiles.disclosure import check_profile_disclosure
from prototypes.capability_profiles.engine import query
from prototypes.research_agent_v2.demo import mock_transport

from app.ai.edu.discovery.models import AgentError, Limits


class SupportBudgetPolicy(ProfileMockPolicy):
    def __init__(self):
        self.checked = set()

    def choose(self, view):
        qualified = [f["fact_id"] for f in view["facts"] if f["qualified"]]
        if not qualified:
            return super().choose(view)
        if view["remaining_tool_attempts"]:
            fact_id = next(f["fact_id"] for f in view["facts"] if f["fact_id"] not in self.checked)
            self.checked.add(fact_id)
            return {"name": "inspect_discovery_support", "arguments": {"fact_id": fact_id}}
        return {
            "name": "finish",
            "arguments": {"selected_fact_ids": qualified, "reason": "completed"},
        }


@pytest.mark.parametrize("support", [False, True])
def test_exhausted_budget_cannot_claim_completion(tmp_path, support):
    result, _, _ = asyncio.run(
        run_profiles(
            "profile-overlap-96",
            tmp_path,
            limits=Limits() if support else Limits(tool_attempts=2),
            transport=mock_transport(SupportBudgetPolicy()) if support else None,
        )
    )
    assert result["tool_attempts"] == (5 if support else 2)
    assert all(t["status"] == "accepted" for t in result["tool_ledger"])
    assert result["uninvestigated_candidates"] == 5
    assert result["stop_reason"] == "tool_budget_exhausted"
    assert result["status"] == "failed"
    assert len(result["profiles"]) == 1  # Preserve the independently reverified partial result.
    assert "调查未完成" in teacher_markdown(result)
    assert "暂无符合展示条件" not in teacher_markdown(result)


def test_runtime_k25_blocks_intersection_and_suppresses_whole_distributions(tmp_path):
    result, book, _ = asyncio.run(
        run_profiles(
            "profile-overlap-96",
            tmp_path,
            limits=Limits(minimum_cell=25),
        )
    )
    groups = [
        set(r.student_id for r in c.discovery.payload.selection.matches) for c in book.candidates
    ]
    assert all(not (0 < len(a & b) < 25) for a in groups for b in groups)
    assert result["profiles"]
    allowed = [query(c.discovery) for c in book.candidates]
    for payload in result["native_requests"]:
        view = json.loads(payload["messages"][-1]["content"])
        assert all(d["arguments"] in allowed for d in view["profile_investigation"]["directions"])
    assert all(p["minimum_cell"] == 25 for p in result["profiles"])
    for profile in result["profiles"]:
        for dimension in profile["dimensions"]:
            for side in ("target", "remainder"):
                cells = dimension[side]["distribution"]
                assert not any(0 < c["count"] < 25 for c in cells)
    assert any(
        d["target"]["distribution"] == [] for p in result["profiles"] for d in p["dimensions"]
    )
    # The technical output must use the same sanitized profile projection.
    assert "witness" not in json.dumps(result["native_requests"])


@pytest.mark.parametrize("minimum_cell,blocked", [(5, False), (25, True)])
def test_same_48_groups_with_24_intersection_obey_runtime_policy(minimum_cell, blocked):
    universe, first, second = frozenset(range(96)), frozenset(range(48)), frozenset(range(24, 72))
    assert len(first ^ second) == 48  # The original symmetric-difference-only gate allowed it.
    kwargs = dict(released=[universe, first], staged=[], minimum_cell=minimum_cell)
    if blocked:
        with pytest.raises(AgentError, match="privacy_limited"):
            check_profile_disclosure([second], **kwargs)
    else:
        assert check_profile_disclosure([second], **kwargs) == [second]


def test_relative_difference_and_higher_order_cells_cannot_be_inferred():
    universe = frozenset(range(96))
    with pytest.raises(AgentError, match="privacy_limited"):
        check_profile_disclosure(
            [range(72)], released=[universe, frozenset(range(48))], staged=[], minimum_cell=25
        )
    universe = frozenset(range(40))
    groups = [frozenset(i for i in universe if (i // 5) & bit) for bit in (1, 2, 4)]
    checked = check_profile_disclosure(groups[:2], released=[universe], staged=[], minimum_cell=6)
    with pytest.raises(AgentError, match="privacy_limited"):
        check_profile_disclosure(groups[2:], released=[universe], staged=checked, minimum_cell=6)
    assert checked == groups[:2]  # Rejecting the third partition cannot mutate staged history.
