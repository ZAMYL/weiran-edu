"""Reproducible local synthetic evidence and native HTTP Mock demonstrations."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.discovery.tools import parse_arguments as legacy_arguments

from .agent import AdaptiveExecutor, run_adaptive, wire
from .analysis import VERSION, Kernel
from .planners import AdaptiveMockPlanner, mock_transport, prepare_synthetic_live
from .synthetic import admit, contest_zip


async def demonstration() -> dict:
    cases = []
    for name in ("five", "contest13", "contest13_complete", "limited", "small"):
        admitted = admit(name)
        kernel = Kernel(admitted.envelope)
        planner = AdaptiveMockPlanner(mock_transport(), kernel.catalog())
        try:
            result, executor = await run_adaptive(name, planner)
            local_statistics = []
            for item_id in kernel.items:
                stats, source = kernel.statistics(item_id)
                local_statistics.append(
                    {"statistics": wire(stats), "actual_source_count": len(source.references)}
                )
            evidence = []
            witnesses = {}
            for fact_id in result.selected_ids:
                fact = executor.facts[fact_id]
                groups = []
                for source in fact.item_sources:
                    refs = executor.resolve(fact_id, source.key)
                    for witness in refs:
                        witnesses[witness.reference_id] = asdict(witness)
                    groups.append(
                        {
                            "metric_key": source.key,
                            "item_id": source.item_id,
                            "actual_count": len(source.participants),
                            "references": [w.reference_id for w in refs],
                        }
                    )
                evidence.append({"fact_id": fact_id, "metrics": groups})
            cases.append(
                {
                    "recipe": name,
                    "synthetic_only": True,
                    "measurement_origin": "offline_mechanism",
                    "source_sha256": admitted.fingerprint,
                    "recognized_item_count": len(kernel.items),
                    "observation_count": len(kernel.data.observations),
                    "rubric_count": len(kernel.data.rubrics),
                    "capabilities": kernel.catalog(),
                    "local_only_statistics": local_statistics,
                    "agent": {
                        "status": result.status,
                        "reason": result.reason,
                        "model_requests": result.model_requests,
                        "tool_attempts": result.tool_attempts,
                        "tool_records": [asdict(t) for t in result.tools],
                        "trace": [asdict(e) for e in result.trace],
                        "verified_model_facts": [f.view for f in result.facts],
                        "final_cards": result.cards,
                        "selected_ids": result.selected_ids,
                        "wall_seconds": result.compute_seconds,
                        "tool_seconds": result.tool_compute_seconds,
                        "input_tokens": None,
                        "output_tokens": None,
                        "cost": None,
                    },
                    "local_only_evidence_index": {
                        "groups": evidence,
                        "witnesses": list(witnesses.values()),
                    },
                }
            )
        finally:
            await planner.aclose()
    probes = []
    attempts = [
        ("query_dimension_relation", {"left": "Q12", "right": "Q01"}),
        (
            "compare_student_groups",
            {
                "conditions": [{"item_id": "Q01", "operator": "<=", "threshold": "20"}],
                "outcomes": ["Q12"],
            },
        ),
        ("get_dimension_statistics", {"batch_id": "foreign"}),
    ]
    for tool, args in attempts:
        executor = AdaptiveExecutor(admit("limited"), "probe-run", Limits())
        try:
            executor.execute(tool, executor.parse(tool, args))
            code = "unexpected_success"
        except AgentError as exc:
            code = str(exc)
        probes.append(
            {
                "kind": "direct_tool_rejection_probe_not_model_choice",
                "tool": tool,
                "error": code,
                "published_facts": len(executor.facts),
                "published_bytes": executor.output_bytes,
            }
        )
    try:
        legacy_arguments("query_dimension_relation", {"left": "Q01", "right": "Q02"})
        old_status = "unexpected_success"
    except AgentError as exc:
        old_status = str(exc)
    return {
        "schema": "edu-adaptive-assessment-demo-v1",
        "rule_version": VERSION,
        "network_model_calls": 0,
        "scope": (
            "local synthetic prototype; not historical ZIP compatibility, "
            "teacher authentication or Qwen quality"
        ),
        "legacy_fixed_tool_question_args": old_status,
        "cases": cases,
        "rejection_probes": probes,
        "synthetic_live_preparation": prepare_synthetic_live("contest13"),
        "limitations": [
            "No real model evaluated; Fake policy only demonstrates result-dependent execution.",
            "No question-to-ability conversion, score sum, causal or growth claim.",
            "Synthetic permission directives are fixtures, never real teacher authorization.",
            "Local evidence/statistics are not the model view or a public DTO.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Offline synthetic adaptive assessment demonstration"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--zip-output", type=Path)
    args = parser.parse_args()
    if any(p and p.exists() for p in (args.output, args.zip_output)):
        parser.error("choose_new_output_paths")
    report = asyncio.run(demonstration())
    text = json.dumps(wire(report), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(str(args.output))
    else:
        print(text)
    if args.zip_output:
        args.zip_output.write_bytes(contest_zip())
        print(str(args.zip_output))
