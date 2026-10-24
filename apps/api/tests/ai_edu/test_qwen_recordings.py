"""Offline replay of checked-in, synthetic-only live receipts. Never calls a model."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from app.ai.edu.models import Proposal, ToolCall
from app.ai.edu.synthetic import build_case
from app.ai.edu.tools import ToolExecutor
from app.ai.edu.validation import validate_proposal

RECORDINGS = Path(__file__).resolve().parents[4] / "evals"


@pytest.mark.parametrize(
    "filename",
    [
        "qwen_smoke_primary_verified.json",
        "qwen_smoke_boundaries.json",
    ],
)
def test_recorded_live_selection_replays_against_computed_facts(filename):
    report = json.loads((RECORDINGS / filename).read_text(encoding="utf-8-sig"))
    assert report["requested_model"] == "qwen3.8-flash"
    for result in report["results"]:
        assert result["status"] == "validated"
        _, source, evidence, rubric = build_case(result["case_id"])
        executor = ToolExecutor(source.dataset, source.access, evidence, rubric)
        for call in result["tool_plan"]:
            output = executor.execute(ToolCall(name=call["tool"], arguments=call["arguments"]))
            assert output.status == "ok"
        proposal = Proposal.model_validate_json(json.dumps(result["validated_model_selection"]))
        validated = validate_proposal(
            proposal,
            executor.facts,
            executor.evidence_ids,
            executor.rubric_ids,
            executor.documents,
        )
        assert validated.cited_fact_ids == tuple(result["card"]["cited_fact_ids"])
        assert [asdict(step) for step in executor.trace] == [
            {**step, "fact_ids": tuple(step["fact_ids"])} for step in result["tool_trace"]
        ]
        for observation in result["card"]["observations"]:
            fact = executor.facts[observation["fact_id"]]
            assert observation["value_exact"] == (
                str(fact.value) if fact.value is not None else None
            )
            assert observation["denominator"] == fact.denominator
            assert observation["rule_version"] == fact.rule_version


def test_recorded_attempts_respect_approved_budget_and_keep_failure_visible():
    all_metrics = []
    for filename in (
        "qwen_smoke_primary.json",
        "qwen_smoke_primary_verified.json",
        "qwen_smoke_boundaries.json",
    ):
        report = json.loads((RECORDINGS / filename).read_text(encoding="utf-8-sig"))
        for result in report["results"]:
            all_metrics.extend(result["model_metrics"])
    assert len(all_metrics) == 6
    assert sum(metric["output_tokens"] for metric in all_metrics) <= 7800
    initial = json.loads((RECORDINGS / "qwen_smoke_primary.json").read_text(encoding="utf-8-sig"))
    assert initial["results"][0]["status"] == "error"
    assert initial["results"][0]["error_code"] == "citation_union_mismatch"
    assert initial["results"][0]["validated_model_selection"] is None
