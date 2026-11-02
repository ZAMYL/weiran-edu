"""Only handoff admission/retention/stop behavior; no new statistics or frozen oracle."""

import asyncio
import json
from dataclasses import asdict, replace

import pytest
from evals.adaptive_acceptance.trusted_handoff import (
    Goal,
    SyntheticHistoryStore,
    build_record,
    context_for,
    continue_history,
    digest,
)

from app.ai.edu.discovery.models import AgentError


@pytest.fixture(scope="module")
def record(tmp_path_factory):
    return asyncio.run(build_record(tmp_path_factory.mktemp("trusted-handoff-minimal")))


def test_abc_preserve_priors_and_use_only_remaining_budget(record):
    expected = {
        "A": (0, 0, "satisfied_by_existing"),
        "B": (1, 1, "new_evidence_obtained"),
        "C": (0, 0, "needs_external_evidence"),
    }
    for label, (new, tools, stop) in expected.items():
        row = record["cases"][label]
        count = row["counts"]
        assert row["status"] == "completed" and row["stop_reason"] == stop
        assert count["retained"] == count["prior"] == 2
        assert count["omitted"] == count["duplicate_tool_attempts"] == 0
        assert count["new_findings"] == new and count["new_tool_attempts"] == tools
        assert count["admission_replay_executions"] == len(row["admission_ledger"]) == 3
        assert count["total_model_requests"] == 4 + count["new_model_requests"] <= 6
        assert count["total_tool_attempts"] == 3 + tools <= 5
        assert not row["replay_old_fact_ids_copied"]
        assert all(f["data_correct"] and f["sources_correct"] for f in row["grade"]["fact_checks"])
        assert not row["grade"]["disclosure_errors"]
        assert not row["grade"]["card_projection_errors"]
        assert row["actual_tokens"] is row["actual_cost_cny"] is None
        assert (
            row["production_authorization"]
            == row["cross_session_disclosure_service"]
            == "not_implemented"
        )
    b = record["cases"]["B"]
    # Independent existing reference: Q13 misses five of twenty, never zero-impute.
    assert b["grade"]["coverage"] == {"count": 3, "denominator": 3}
    new = b["new_issued_facts"]
    assert len(new) == 1 and new[0]["metrics"]["item_id"] == "Q13"
    assert new[0]["metrics"]["denominator"] == 20
    assert new[0]["metrics"]["counts"]["missing"] == 5
    assert len(b["model_selected"]) == 1 and len(b["cards"]) == 3
    c = record["cases"]["C"]
    assert c["missing_evidence"] == ["rubric_criteria", "original_task_evidence"]
    assert c["native_stop_reason"] == "completed"  # Separate observation and evidence-gap states.
    assert not c["tools"] and record["real_qwen"] == "not_run"


def test_http_goal_seam_contains_no_case_or_oracle_and_keeps_actual_history(record):
    for row in record["cases"].values():
        for request in row["http_transcript"]:
            view = json.loads(request["messages"][-1]["content"])
            assert view["task"] == {
                "goal": row["goal"],
                "rubric_tool_available": False,
                "task_evidence_tool_available": False,
            }
            raw = json.dumps(request)
            for forbidden in (
                "case_id",
                "oracle",
                "prior_ref",
                "source_sha256",
                "evidence_id",
                "participant_id",
                "observations",
                "original_ids",
            ):
                assert f'"{forbidden}"' not in raw
    requests = record["cases"]["B"]["http_transcript"]
    assert len(requests) == 2 and len(requests[1]["messages"]) > len(requests[0]["messages"])
    assert requests[0]["proposal"]["name"] == "inspect_item_quality"
    assert requests[1]["proposal"]["name"] == "finish"


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    store = SyntheticHistoryStore()
    state = tmp_path_factory.mktemp("history-server")
    reference, prior = asyncio.run(store.create("five", state))
    return store, reference, prior


def clone(saved):
    store = SyntheticHistoryStore()
    store._entries = dict(saved[0]._entries)
    return store, saved[1]


@pytest.mark.parametrize(
    "field,value",
    [
        ("batch_id", "other-batch"),
        ("snapshot_id", "0" * 64),
        ("source_sha256", "1" * 64),
        ("scope_sha256", "2" * 64),
        ("rule_version", "unreviewed-rule"),
        ("policy_sha256", "3" * 64),
        ("minimum_cell", 1),
        ("purpose", "production"),
        ("recipe", "contest13"),
    ],
)
def test_wrong_history_context_never_publishes_or_consumes_valid_handle(saved, field, value):
    store, reference = clone(saved)
    with pytest.raises(AgentError, match="history_context_mismatch"):
        store.admit(reference, replace(context_for("five"), **{field: value}))
    assert reference not in store._used


def test_arbitrary_json_and_old_fact_ids_are_not_history_capabilities(saved):
    store, _ = clone(saved)
    for value in ({"confirmed": True, "facts": []}, saved[2].run.selected_ids[0], "history-forged"):
        with pytest.raises(AgentError, match="unknown_history_reference"):
            store.admit(value, context_for("five"))


@pytest.mark.parametrize(
    "corruption,code",
    [("proof", "history_replay_mismatch"), ("disclosure", "history_disclosure_mismatch")],
)
def test_recomputed_integrity_hash_cannot_hide_wrong_sources_or_missing_disclosure(
    saved, corruption, code
):
    store, reference = clone(saved)
    history, _ = store._entries[reference]
    changed = (
        replace(history, proofs=("forged-source", *history.proofs[1:]))
        if corruption == "proof"
        else replace(history, disclosed=())
    )
    # Fault in server storage, with a matching checksum: semantic replay must still reject it.
    store._entries[reference] = changed, digest(asdict(changed))
    with pytest.raises(AgentError, match=code):
        store.admit(reference, context_for("five"))
    assert reference in store._used
    assert len(saved[2].executor.facts) == 3  # Failure creates no mutations in the prior ledger.


def test_handle_is_one_use_and_does_not_replenish_allowance(saved):
    store, reference = clone(saved)
    executor, history, *_ = store.admit(reference, context_for("five"))
    assert executor.limits.model_requests == 6 - history.model_requests == 2
    assert executor.limits.tool_attempts == 5 - history.tool_attempts == 2
    assert tuple(executor.released) == tuple(saved[2].executor.released)
    with pytest.raises(AgentError, match="history_already_consumed"):
        store.admit(reference, context_for("five"))


@pytest.mark.parametrize(
    "fault,reason", [("old-id", "unknown_fact_id"), ("duplicate", "repeated_tool")]
)
def test_failed_investigation_is_explicit_and_cannot_delete_verified_history(
    saved, tmp_path, fault, reason
):
    store, reference = clone(saved)
    old = saved[2].run.selected_ids[0]

    def inject(_choice, view):
        if fault == "old-id":
            return {
                "name": "finish",
                "arguments": {"selected_fact_ids": [old], "reason": "completed"},
            }
        return {"name": "get_dimension_statistics", "arguments": {}}

    row = asyncio.run(
        continue_history(
            store, reference, context_for("five"), Goal.OBSERVATIONS, tmp_path, fault=inject
        )
    )
    assert row["status"] == "failed" and row["stop_reason"] == "investigation_failed"
    assert row["native_stop_reason"] == reason
    assert row["counts"]["retained"] == 2 and row["counts"]["omitted"] == 0
    assert row["counts"]["new_findings"] == 0
    assert not row["new_issued_facts"] and len(row["cards"]) == 2
    assert row["counts"]["total_model_requests"] <= 6
    assert row["counts"]["total_tool_attempts"] <= 5
