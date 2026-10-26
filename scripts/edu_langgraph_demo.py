"""Archive actual offline Fake/Mock execution; never contact a model or read student files."""

import argparse
import asyncio
import json
import sys
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from evals.discovery_langgraph.comparison import behavior  # noqa: E402

from app.ai.edu.discovery import AdaptiveFake, run_agent  # noqa: E402
from app.ai.edu.discovery.demo import CrossBatchProbe, ForgedReferenceProbe, project  # noqa: E402
from app.ai.edu.discovery.langgraph_runtime import _build_graph, run_agent_langgraph  # noqa: E402
from app.ai.edu.discovery.mock_http import MockQwenPlanner, adaptive_transport  # noqa: E402


def trace_projection(run) -> dict:
    return {
        **project(run),
        "proposed_ids": run.proposed_ids,
        "tool_compute_seconds": run.tool_compute_seconds,
        "non_tool_seconds": max(0, run.compute_seconds - run.tool_compute_seconds),
    }


async def demonstrate() -> dict:
    comparisons = []
    for case in ("coupled53", "constant17"):
        for transport in ("AdaptiveFake", "httpx.MockTransport"):
            outputs = []
            for runner in (run_agent, run_agent_langgraph):
                planner = (
                    AdaptiveFake()
                    if transport == "AdaptiveFake"
                    else MockQwenPlanner(adaptive_transport())
                )
                try:
                    outputs.append(await runner(case, planner))
                finally:
                    if isinstance(planner, MockQwenPlanner):
                        await planner.aclose()
            comparisons.append(
                {
                    "case": case,
                    "transport": transport,
                    "equivalent": behavior(outputs[0]) == behavior(outputs[1]),
                    "baseline": trace_projection(outputs[0]),
                    "langgraph": trace_projection(outputs[1]),
                    "source_digests": [f["source_digest"] for f in behavior(outputs[1])["facts"]],
                }
            )
    probes = []
    for planner_type in (CrossBatchProbe, ForgedReferenceProbe):
        old = await run_agent("coupled53", planner_type())
        new = await run_agent_langgraph("coupled53", planner_type())
        probes.append(
            {
                "probe": planner_type.__name__,
                "equivalent": behavior(old) == behavior(new),
                "baseline": trace_projection(old),
                "langgraph": trace_projection(new),
            }
        )
    return {
        "schema": "edu.langgraph.offline.comparison.v1",
        "synthetic_only": True,
        "real_model_quality": "not_evaluated",
        "input_tokens": None,
        "output_tokens": None,
        "cost": None,
        "checkpointer": None,
        "cloud_tracing": False,
        "baseline_commit": "91de608a30409f92809e63d7a820b189cedcb1fd",
        "reference_pr47_commit": "91463121db520237301c317589e619b35e9eac37",
        "versions": {name: version(name) for name in ("langgraph", "langchain-core", "langsmith")},
        "normalization": [
            "run_id references",
            "compute_seconds",
            "tool_compute_seconds",
            "graph_node events removed and semantic sequences renumbered",
        ],
        "source_comparison": "Full metric membership/dimensions/evidence digests; no row archive",
        "graph_mermaid": _build_graph().get_graph().draw_mermaid(),
        "comparisons": comparisons,
        "probes": probes,
        "equivalent": all(c["equivalent"] for c in [*comparisons, *probes]),
        "default_runner": "legacy run_agent; opt-in only, no production rollout",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output is not None and (args.output.suffix != ".json" or args.output.exists()):
        print(json.dumps({"error": "NEW_JSON_OUTPUT_REQUIRED"}))
        return 2
    result = asyncio.run(demonstrate())
    rendered = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
    if args.output is None:
        print(rendered)
    else:
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            output.write(rendered + "\n")
        print(
            json.dumps(
                {
                    "schema": result["schema"],
                    "equivalent": result["equivalent"],
                    "comparisons": len(result["comparisons"]),
                    "probes": len(result["probes"]),
                }
            )
        )
    return 0 if result["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
