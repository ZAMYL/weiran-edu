"""Run from apps/api: uv run --locked python -m app.ai.edu.discovery.demo."""

import asyncio
import json
from dataclasses import asdict

from .mock_http import MockQwenPlanner, adaptive_transport
from .orchestrator import run_agent
from .planners import AdaptiveFake


def project(run) -> dict:
    # Never asdict(AgentRun): that would include private metric sources.
    return {
        "run_id": run.run_id,
        "status": run.status,
        "reason": run.reason,
        "model_requests": run.model_requests,
        "tool_attempts": run.tool_attempts,
        "elapsed_seconds": run.compute_seconds,
        "measurement_origin": run.measurement_origin,
        "input_tokens": None,
        "output_tokens": None,
        "cost": None,
        "tool_sequence": [asdict(t) for t in run.tools],
        "facts": [f.view for f in run.facts],
        "trace": [asdict(e) for e in run.trace],
        "cards": run.cards,
    }


class CrossBatchProbe(AdaptiveFake):
    def choose(self, view):
        if view["last_result"] is None:
            return {
                "name": "get_dimension_statistics",
                "arguments": {"batch_id": "synthetic-foreign-batch"},
            }
        return {
            "name": "finish",
            "arguments": {"selected_fact_ids": [], "reason": "no_valid_findings"},
        }


class ForgedReferenceProbe(AdaptiveFake):
    def choose(self, view):
        return {
            "name": "finish",
            "arguments": {"selected_fact_ids": ["forged-fact-id"], "reason": "completed"},
        }


async def demo() -> dict:
    outputs = []
    for name in ("coupled53", "constant17"):
        planner = MockQwenPlanner(adaptive_transport())
        try:
            output = await run_agent(name, planner)
            outputs.append(
                {
                    "case": name,
                    "transport": "httpx.MockTransport",
                    "run": project(output),
                    "http_metrics": [asdict(m) for m in planner.metrics],
                }
            )
        finally:
            await planner.aclose()
    probes = [
        project(await run_agent("coupled53", CrossBatchProbe())),
        project(await run_agent("coupled53", ForgedReferenceProbe())),
    ]
    return {
        "schema": "edu-discovery-runtime-demo-v1",
        "disclaimer": (
            "Offline Fake/Mock mechanism only; not Qwen quality, no paid requests, no teacher data."
        ),
        "runs": outputs,
        "scripted_security_probes": probes,
        "formal_evaluate": "not_run: PR #47 unmerged; summary/support and trace grading pending",
    }


def main():
    print(json.dumps(asyncio.run(demo()), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
