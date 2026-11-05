"""Runnable server-side Provider example; no public endpoint and no remote model."""

import argparse
import json
from pathlib import Path

from app.ai.edu.discovery.models import AgentError

from prototypes.research_agent_v2.runtime import fact_record

from .provider import ProviderProbe, SyntheticProvider


def example():
    provider = SyntheticProvider()
    data = provider.source.envelope.data
    lease = provider.acquire(
        "synthetic-teacher",
        data.batch.batch_id,
        provider.source.fingerprint,
        "synthetic-provider-demo",
    )
    probe = ProviderProbe(provider, lease)
    metadata = probe.metadata()
    goal = probe.goal("比较Q01、Q02的组合与Q03的群体差异")
    probe.execute("get_dimension_statistics", {})
    conditions = [
        {"item_id": "Q01", "operator": "<=", "threshold": "0"},
        {"item_id": "Q02", "operator": "<=", "threshold": "0"},
    ]
    probe.execute("compare_student_groups", {"conditions": conditions, "outcomes": ["Q03"]})
    verified = [fact_record(probe.executor, f) for f in probe.executor.facts.values()]
    group = list(probe.executor.facts.values())[-1]
    source = next(s for s in group.item_sources if s.key == "target.valid")
    witness = provider.replay_witness(probe.binding, source.references[0])
    cards = probe.cards([group.fact_id])
    ledger = list(probe.executor.attempt_ledger)
    provider.enabled = False
    try:
        probe.execute("get_dimension_statistics", {})
    except AgentError as error:
        revoked = str(error)
    return {
        "origin": "closed_synthetic_provider_example",
        "initial_context": metadata,
        "controlled_goal": goal,
        "verified_facts": verified,
        "conditions": conditions,
        "internal_card_projection": cards,
        "tool_execution_ledger": ledger,
        "witness_check": {
            "source_sha256_matches_snapshot": witness.source_sha256 == lease.binding.snapshot_id,
            "metric_key": source.key,
            "record_count": len(source.references),
            "raw_witness_and_locator_included": False,
        },
        "revoked_request": {"status": "rejected", "error": revoked},
        "production_authentication": "not_implemented",
        "remote_model_allowed": False,
        "formal_http_dto": "not_defined",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = example()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print("Provider source replay and revocation example completed; remote model disabled.")


if __name__ == "__main__":
    main()
