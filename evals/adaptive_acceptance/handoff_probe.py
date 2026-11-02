"""Synthetic failure reproductions for trusted discovery handoff; not an Agent upgrade."""

import argparse
import asyncio
import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.discovery_v1.cases import cases
from prototypes.adaptive_assessment.agent import SCHEMAS

from .collector import collect
from .grading import grade_native
from .reference import frozen_spec
from .runner import check_admitted_input, spec_for_recipe, verify_dependencies


def _signatures(captured, grade):
    checked = {v["fact_id"]: v for v in grade["fact_checks"]}
    if grade["card_projection_errors"]:
        return set()
    return {
        checked[i]["signature"]
        for i in captured.run.selected_ids
        if i in checked
        and checked[i]["qualified"]
        and checked[i]["data_correct"]
        and checked[i]["sources_correct"]
    }


def _queries(captured):
    return {
        json.dumps([v["tool"], v["arguments"]], sort_keys=True)
        for v in captured.audit["tool_ledger"]
        if v["status"] == "accepted"
    }


def _record(captured, spec):
    grade = grade_native(spec, captured)
    return {
        "grade": grade,
        "selected_signatures": sorted(_signatures(captured, grade)),
        "audit": captured.audit,
    }


def _difference(prior, current):
    left, right = set(prior["selected_signatures"]), set(current["selected_signatures"])
    old_ids = set(prior["audit"]["selected_ids"])
    new_ids = set(current["audit"]["selected_ids"])
    return {
        "prior_correct_findings": len(left),
        "retained_semantics": len(left & right),
        "retained_original_fact_ids": len(old_ids & new_ids),
        "new_verified_findings": len(right - left),
        "omitted_prior_findings": len(left - right),
        "cross_run_republished_semantics": len(left & right),
    }


def _after_relation(mode):
    """A labelled fault-injection probe triggered by a real tool result, not a case name."""

    def injected(chosen, _round, view):
        relation = next((f for f in view["facts"] if f.get("type") == "item_relation"), None)
        if relation is None:
            return chosen
        if mode == "unavailable_rubric":
            return {"name": "get_rubric_criteria", "arguments": {}}
        return {
            "name": "finish",
            "arguments": {
                "selected_fact_ids": [relation["fact_id"]] if mode == "keep_and_gap" else [],
                "reason": "insufficient_data",
            },
            "basis_fact_ids": [relation["fact_id"]],
        }

    return injected


async def build_probe(state: Path) -> dict:
    pins = verify_dependencies()
    five = frozen_spec(next(c for c in cases() if c.case_id == "group-53"))
    missing = spec_for_recipe("contest13")
    # collect admits the closed synthetic registry; no upload/data/provider argument exists.
    from prototypes.adaptive_assessment.synthetic import admit

    check_admitted_input(five, admit("five"))
    check_admitted_input(missing, admit("contest13"))
    original = await collect("five", state, profile="fixed")
    original_ids = tuple(original.run.selected_ids)
    original_digest = sha256(json.dumps(original.audit, sort_keys=True).encode()).hexdigest()
    prior = _record(original, five)
    fresh = await collect("five", state)

    def select_old_ids(_chosen, _round, _view):
        # Genuine prior IDs only; never insert a prior Fact into the new executor.
        return {
            "name": "finish",
            "arguments": {"selected_fact_ids": list(original_ids), "reason": "completed"},
        }

    rejected = await collect("five", state, fault=select_old_ids)
    fresh_record = _record(fresh, five)
    case_a = {
        "prior": prior,
        "fresh_without_handoff": fresh_record,
        "untrusted_previous_id_attempt": _record(rejected, five),
        "comparison": _difference(prior, fresh_record),
        "recomputed_same_tool_queries": len(_queries(original) & _queries(fresh)),
        "original_ledger_unchanged": (
            original_ids == original.run.selected_ids
            and original_digest
            == sha256(json.dumps(original.audit, sort_keys=True).encode()).hexdigest()
        ),
        "safe_handoff_supported": False,
        "goal_status": "not_met_no_admission_mechanism",
    }
    old_b = await collect("contest13", state, profile="fixed")
    new_b = await collect("contest13", state)
    prior_b, current_b = _record(old_b, missing), _record(new_b, missing)
    case_b = {
        "prior": prior_b,
        "fresh_investigation": current_b,
        "comparison": _difference(prior_b, current_b),
        "unspent_tool_attempts": 5 - new_b.run.tool_attempts,
        "unspent_model_decisions": 6 - new_b.run.model_requests,
        "goal_status": "correct_new_quality_fact_but_prior_relation_omitted",
        "safe_continuation": "not_run_no_admission_mechanism",
    }
    case_c = {}
    for mode in ("empty_gap", "keep_and_gap", "unavailable_rubric"):
        captured = await collect("five", state, fault=_after_relation(mode))
        case_c[mode] = _record(captured, five)
    case_c.update(
        {
            "probe_kind": "scripted_negative_protocol_probes_not_autonomous_decisions",
            "rubric_tool_in_adaptive_whitelist": "get_rubric_criteria" in SCHEMAS,
            "goal_status": "not_met_no_preserve_and_evidence_gap_outcome",
        }
    )
    return {
        "kind": "handoff_gap_reproduction_v1",
        "dependency_pins": pins,
        "execution_origin": "fixed_policy_and_http_mock_no_real_model",
        "prior_fact_objects_imported": False,
        "oracle_changed": False,
        "existing_result_files_changed": False,
        "paid_api_calls": 0,
        "real_qwen": "not_run",
        "A": case_a,
        "B": case_b,
        "C": case_c,
        "go_no_go": "do_not_expand_or_run_paid_effectiveness_test_until_handoff_contract_review",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.suffix.lower() != ".json":
        parser.error("new_JSON_output_required_preserve_failures")
    with TemporaryDirectory(prefix="edu-handoff-probe-") as state:
        report = asyncio.run(build_probe(Path(state)))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "A": report["A"]["comparison"],
                "B": report["B"]["comparison"],
                "A_status": report["A"]["goal_status"],
                "C_status": report["C"]["goal_status"],
                "go_no_go": report["go_no_go"],
                "real_qwen": "not_run",
            }
        )
    )
    # Exit 1 retains the explicit product gaps; successful reproduction is not feature acceptance.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
