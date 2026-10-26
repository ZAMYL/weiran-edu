"""Contract-factory tests without importing or editing PR #47 frozen oracles."""

import asyncio
import importlib.util
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.ai.edu.discovery import AdaptiveFake
from app.ai.edu.discovery.demo import demo
from app.ai.edu.discovery.synthetic import build, rows

ADAPTER = Path(__file__).resolve().parents[4] / "evals/discovery_agent_v1/adapter.py"
spec = importlib.util.spec_from_file_location("runtime_candidate_adapter", ADAPTER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def factory():
    def query(kind, left="", right="", conditions=(), outcome="", side=""):
        return NS(
            kind=kind, left=left, right=right, conditions=conditions, outcome=outcome, side=side
        )

    def claim(*args):
        return NS(
            **dict(
                zip(
                    (
                        "fact_id",
                        "run_id",
                        "batch_id",
                        "snapshot_id",
                        "rule_version",
                        "query",
                        "values",
                        "bindings",
                    ),
                    args,
                    strict=True,
                )
            )
        )

    def candidate(run_id, status, **kwargs):
        return NS(run_id=run_id, status=status, **kwargs)

    return NS(
        Query=query,
        Predicate=lambda d, o, t: NS(dimension=d, operator=o, threshold=t),
        Claim=claim,
        Binding=lambda i, e: NS(identity=i, evidence=e),
        CandidateRun=candidate,
    )


def source(name="coupled53"):
    return NS(
        rows=rows(name),
        distractors=(),
        batch_id="synthetic-batch-" + "a" * 20,
        snapshot_id="synthetic-snapshot-" + "b" * 20,
        source_id="synthetic-source-" + "c" * 20,
    )


def test_candidate_run_values_and_bindings_come_from_actual_runtime():
    adapter = module.AgentCandidate(factory())
    case = source()
    result = adapter.run(case, "C_agent")
    assert result.status == "completed" and result.model_requests == 5
    assert result.measurement_origin == "offline_mechanism"
    assert result.cost is result.input_tokens is result.output_tokens is None
    group = next(c for c in result.ledger if c.query.kind == "group")
    assert group.batch_id == case.batch_id and group.snapshot_id == case.snapshot_id
    assert group.values["target_count"] == 26 and group.values["population_count"] == 53
    assert group.values["IMPLEMENTATION"]["difference"] == Fraction(4)
    assert len(group.bindings["target.IMPLEMENTATION"]) == 26
    assert all(
        b.identity[0] == case.batch_id and b.identity[3] == case.source_id
        for b in group.bindings["target.IMPLEMENTATION"]
    )
    assert result.presented_ids == result.selected_ids == (group.fact_id,)
    assert len(adapter.runs[result.run_id].trace) > len(result.tool_trace)
    # Summary/support facts remain in the companion ledger; do not falsely pass old grading.
    assert len(adapter.runs[result.run_id].facts) > len(result.ledger)


def test_unregistered_input_not_run_not_fake_success():
    adapter = module.AgentCandidate(factory())
    case = source()
    case.rows = ((2, 2, 2, 2, 2),) * 7
    outcome = adapter.run(case, "C_agent")
    assert outcome.status == "not_run" and outcome.reason == "unregistered_synthetic_recipe"
    assert not adapter.runs


def test_context_rejects_private_text_and_incompatible_profile():
    with pytest.raises(ValueError, match="invalid_synthetic_context"):
        build("coupled53", context=("real-teacher-identity", "private-snapshot", "private-source"))
    with pytest.raises(ValueError, match="agent_profile_required"):
        module.AgentCandidate(factory()).run(source(), "A_default")


def test_adapter_preserves_rejected_original_proposal():
    class Forged(AdaptiveFake):
        def choose(self, view):
            return {
                "name": "finish",
                "arguments": {"selected_fact_ids": ["fake-fact"], "reason": "completed"},
            }

    result = module.AgentCandidate(factory(), Forged).run(source(), "C_agent")
    assert result.status == "failed"
    assert result.selected_ids == ("fake-fact",) and result.presented_ids == ()


def test_demo_contains_two_real_paths_and_rejected_cases_without_private_sources():
    result = asyncio.run(demo())
    assert result["runs"][0]["run"]["tool_attempts"] == 4
    assert result["runs"][1]["run"]["tool_attempts"] == 1
    assert (
        result["scripted_security_probes"][0]["tool_sequence"][0]["error"]
        == "invalid_tool_arguments"
    )
    assert result["scripted_security_probes"][1]["reason"] == "unknown_fact_id"
    assert all(run["run"]["input_tokens"] is None for run in result["runs"])
    import json

    assert "synthetic-member" not in json.dumps(result)
