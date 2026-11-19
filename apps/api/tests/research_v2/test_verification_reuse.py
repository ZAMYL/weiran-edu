"""Full-Fact replay reuse, including hidden sources, mutation and current authority."""

import json
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest
from app.ai.edu.discovery.models import AgentError, Limits

from prototypes.adaptive_assessment.analysis import Kernel
from prototypes.adaptive_assessment.data import PrototypeError
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.research_agent_v2.runtime import ResearchExecutor


def issued():
    executor = ResearchExecutor(admit("five"), "immutable-proof", Limits())
    fact = executor.execute(
        "get_dimension_statistics", executor.parse("get_dimension_statistics", {})
    )[0]
    executor._verify(fact)
    return executor, fact


def test_unchanged_full_ledger_does_not_reconstruct_kernel(monkeypatch):
    executor, fact = issued()
    count = executor.replay_executions
    original = Kernel.__init__
    builds = []

    def construct(self, *args, **kwargs):
        builds.append(1)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Kernel, "__init__", construct)
    for _ in range(8):
        executor._verify(fact)
        executor.verify_all()
    assert executor.replay_executions == count
    assert builds == []
    next_fact = executor.execute(
        "inspect_item_quality", executor.parse("inspect_item_quality", {"item_id": "I01"})
    )[0]
    executor._verify(next_fact)
    assert builds and executor.replay_executions > count


@pytest.mark.parametrize(
    "field", ["view", "view_sha256", "proof_sha256", "fact_id", "discovery", "query"]
)
def test_every_fact_field_invalidates_prior_verification(field):
    executor, fact = issued()
    if field == "view":
        view = deepcopy(fact.view)
        view["items"][0]["valid_count"] = 999
        forged = replace(
            fact,
            view=view,
            view_sha256=sha256(
                json.dumps(view, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
            ).hexdigest(),
        )
    elif field == "query":
        forged = replace(fact, query={"tool": fact.tool, "arguments": {"item_ids": ["I02"]}})
    else:
        forged = replace(fact, **{field: "forged"})
    executor.facts[fact.fact_id] = forged
    with pytest.raises(AgentError, match="source_validation_failed"):
        executor._verify(forged)


def test_hidden_calculation_sources_cannot_reuse_cached_success():
    executor, _ = issued()
    fact = executor.execute(
        "compare_student_groups",
        executor.parse(
            "compare_student_groups",
            {
                "conditions": [{"item_id": "I01", "operator": "<=", "threshold": "2.75"}],
                "outcomes": ["I03"],
            },
        ),
    )[0]
    executor._verify(fact)
    fake_source = replace(fact.calculation.sources[0], references=("forged-reference",))
    forged = replace(
        fact,
        calculation=replace(fact.calculation, sources=(fake_source, *fact.calculation.sources[1:])),
    )
    assert repr(forged.calculation) == repr(fact.calculation)
    executor.facts[fact.fact_id] = forged
    with pytest.raises(AgentError, match="source_validation_failed"):
        executor._verify(forged)


@pytest.mark.parametrize("field", ["released", "output_bytes"])
def test_disclosure_mutation_invalidates_reuse(field):
    executor, fact = issued()
    if field == "released":
        executor.released.append(frozenset((executor.kernel.participants[0],)))
    else:
        executor.output_bytes += 1
    with pytest.raises(AgentError, match="disclosure_history_mismatch"):
        executor._verify(fact)


def test_cached_ledger_still_checks_current_authority(monkeypatch):
    executor, fact = issued()

    def revoked(_source):
        raise AgentError("current_authority_revoked")

    monkeypatch.setattr(type(executor.source), "validate", revoked)
    with pytest.raises(AgentError, match="current_authority_revoked"):
        executor._verify(fact)
    with pytest.raises(AgentError, match="current_authority_revoked"):
        executor.verify_all()


def test_source_failure_retains_existing_error_semantics(monkeypatch):
    executor, fact = issued()

    def corrupt(_source):
        raise PrototypeError("corrupt_source")

    monkeypatch.setattr(type(executor.source), "validate", corrupt)
    with pytest.raises(AgentError, match="source_validation_failed"):
        executor._verify(fact)


def test_unissued_fact_cannot_use_verified_ledger():
    executor, fact = issued()
    with pytest.raises(AgentError, match="source_validation_failed"):
        executor._verify(replace(fact))


def test_numeric_equality_does_not_replace_canonical_hash_validation():
    executor, fact = issued()
    view = deepcopy(fact.view)
    view["items"][0]["valid_count"] = float(view["items"][0]["valid_count"])
    forged = replace(fact, view=view)
    assert forged == fact  # Dataclass equality alone ignores int/float representation.
    executor.facts[fact.fact_id] = forged
    with pytest.raises(AgentError, match="source_validation_failed"):
        executor._verify(forged)
