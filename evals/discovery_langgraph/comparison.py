"""Normalize only run IDs, timing and graph-only events; retain rejected decisions."""

import hashlib
import json
from dataclasses import asdict

from app.ai.edu.discovery.models import AgentRun


def behavior(run: AgentRun) -> dict:
    semantic_trace = []
    for event in run.trace:
        if event.phase != "graph_node":
            semantic_trace.append({**asdict(event), "sequence": len(semantic_trace) + 1})
    result = {
        "status": run.status,
        "reason": run.reason,
        "cards": run.cards,
        "selected_ids": run.selected_ids,
        "proposed_ids": run.proposed_ids,
        "model_requests": run.model_requests,
        "tool_attempts": run.tool_attempts,
        "measurement_origin": run.measurement_origin,
        "tools": [asdict(tool) for tool in run.tools],
        "trace": semantic_trace,
        "facts": [
            {
                "tool": fact.tool,
                "view": fact.view,
                "qualified": fact.discovery is not None,
                "discovery_id": fact.discovery.discovery_id if fact.discovery else None,
                # Include complete metric membership/dimension/evidence binding in the digest,
                # but never archive the underlying student references or scoring rows.
                "source_digest": hashlib.sha256(
                    json.dumps(
                        [asdict(source) for source in fact.metric_sources],
                        sort_keys=True,
                        default=lambda value: value.value,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            }
            for fact in run.facts
        ],
    }

    def normalize(value):
        if isinstance(value, str):
            return value.replace(run.run_id, "run-normalized")
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    return normalize(result)
