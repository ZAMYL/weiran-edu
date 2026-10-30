"""Reproduce current handoff gaps; green tests do not mean feature acceptance."""

import asyncio

import pytest
from evals.adaptive_acceptance.handoff_probe import build_probe


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    report = asyncio.run(build_probe(tmp_path_factory.mktemp("trusted-handoff")))
    rows = [
        report["A"]["prior"],
        report["A"]["fresh_without_handoff"],
        report["A"]["untrusted_previous_id_attempt"],
        report["B"]["prior"],
        report["B"]["fresh_investigation"],
        *(report["C"][name] for name in ("empty_gap", "keep_and_gap", "unavailable_rubric")),
    ]
    for row in rows:
        assert row["audit"]["model_requests"] <= 6
        assert row["audit"]["tool_attempts"] <= 5
        assert row["audit"]["paid_api_calls"] == 0
        assert not row["grade"]["disclosure_errors"]
        for metric in ("data_accuracy", "source_accuracy"):
            assert row["grade"][metric]["count"] == row["grade"][metric]["denominator"]
    return report


def test_previous_ids_are_rejected_not_admitted_or_forged(probe):
    a = probe["A"]
    assert a["untrusted_previous_id_attempt"]["audit"]["reason"] == "unknown_fact_id"
    assert not a["untrusted_previous_id_attempt"]["audit"]["issued_facts"]
    assert a["comparison"]["retained_semantics"] == 2
    assert a["comparison"]["retained_original_fact_ids"] == 0
    assert a["comparison"]["cross_run_republished_semantics"] == 2
    assert a["recomputed_same_tool_queries"] == 3
    assert a["original_ledger_unchanged"]
    assert not a["safe_handoff_supported"]
    assert not probe["prior_fact_objects_imported"]


def test_new_quality_finding_does_not_hide_missing_prior_relation(probe):
    b = probe["B"]
    assert b["comparison"]["new_verified_findings"] == 1
    assert b["comparison"]["omitted_prior_findings"] == 1
    assert b["comparison"]["retained_semantics"] == 1
    assert b["unspent_tool_attempts"] == b["unspent_model_decisions"] == 1
    assert b["prior"]["grade"]["coverage"] == {"count": 2, "denominator": 3}
    assert b["fresh_investigation"]["grade"]["coverage"] == {"count": 2, "denominator": 3}
    assert b["safe_continuation"].startswith("not_run")


def test_evidence_gap_cannot_keep_valid_discovery_in_current_finish_schema(probe):
    c = probe["C"]
    assert c["empty_gap"]["audit"]["reason"] == "insufficient_data"
    assert c["empty_gap"]["audit"]["status"] == "completed"
    assert len(c["empty_gap"]["audit"]["issued_facts"]) == 2
    assert not c["empty_gap"]["audit"]["cards"]
    assert c["keep_and_gap"]["audit"]["reason"] == "inconsistent_stop_reason"
    assert not c["keep_and_gap"]["audit"]["cards"]
    assert c["unavailable_rubric"]["audit"]["status"] == "failed"
    assert not c["rubric_tool_in_adaptive_whitelist"]
    assert c["probe_kind"].startswith("scripted_negative")
    assert probe["real_qwen"] == "not_run"
