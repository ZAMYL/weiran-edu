"""Observe existing verification calls without weakening or caching any check."""

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from prototypes.adaptive_assessment.analysis import Kernel
from prototypes.adaptive_assessment.synthetic import AdmittedSynthetic
from prototypes.research_agent_v2.demo import offline
from prototypes.research_agent_v2.runtime import ResearchExecutor


async def profile():
    events = []
    active = {}
    code = ResearchExecutor._verify.__code__
    calculations = Counter()
    watched = {
        method.__code__: name
        for name, method in (
            ("Kernel.statistics", Kernel.statistics),
            ("Kernel.relation", Kernel.relation),
            ("Kernel.group", Kernel.group),
            ("AdmittedSynthetic.validate", AdmittedSynthetic.validate),
        )
    }

    def observe(frame, event, arg):
        if event == "call" and frame.f_code in watched:
            calculations[watched[frame.f_code]] += 1
        if frame.f_code is not code:
            return
        executor = frame.f_locals["self"]
        if event == "call":
            active[id(frame)] = (
                executor.replay_executions,
                perf_counter(),
                frame.f_back.f_code.co_name,
                len(executor.facts),
            )
        elif event == "return":
            before, started, caller, count = active.pop(id(frame))
            events.append(
                {
                    "caller": caller,
                    "facts_in_ledger": count,
                    "replay_executions": executor.replay_executions - before,
                    "elapsed_seconds": perf_counter() - started,
                }
            )

    if sys.getprofile() is not None:
        raise RuntimeError("profiling_already_active")
    with TemporaryDirectory(prefix="research-v22-profile-") as state:
        sys.setprofile(observe)
        try:
            result, _ = await offline(
                "research-v2-capability", "比较I01、I02的组合与I03的群体差异", Path(state)
            )
        finally:
            sys.setprofile(None)
    totals = Counter()
    calls = Counter()
    for event in events:
        totals[event["caller"]] += event["replay_executions"]
        calls[event["caller"]] += 1
    return {
        "origin": "offline_mechanism",
        "real_qwen": "not_run",
        "model_requests": result["model_requests"],
        "tool_attempts": result["tool_attempts"],
        "reported_replays": result["verification_replay_executions"],
        "observed_replays": sum(totals.values()),
        "verify_calls_by_caller": dict(calls),
        "replays_by_caller": dict(totals),
        "events": events,
        "checks_cached_or_removed": 0,
        "kernel_and_source_calls": dict(calculations),
        "timing_limitation": "sys.setprofile adds overhead; do not compare to unprofiled latency",
        "decision": "retain_executor_checks",
        "reason": [
            "mutable_fact_views_and_queries",
            "source_and_disclosure_history_can_change",
            "future_provider_revocation_requires_fresh_check",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(profile())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != "events"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
