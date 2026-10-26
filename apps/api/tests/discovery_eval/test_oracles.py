"""Independent literal anchors; no SUT used as an expected-value generator."""

import ast
import copy
import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction as F
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from evals.discovery_v1.cases import cases  # noqa: E402
from evals.discovery_v1.contracts import RULE_VERSION, CandidateRun, Claim, Predicate, Query  # noqa: E402
from evals.discovery_v1.grading import grade, grade_claim, trace_request_valid  # noqa: E402
from evals.discovery_v1.reference import case_digest, manifest, reference, score, wire  # noqa: E402
from evals.discovery_v1.runner import evaluate, protocol_probes  # noqa: E402

CASES = {c.case_id: c for c in cases()}


def claim_for(case):
    q = case.targets[0]
    r = reference(case, q)
    return Claim(
        "issued-fact",
        "unit-run",
        case.batch_id,
        case.snapshot_id,
        RULE_VERSION,
        q,
        r.values,
        r.bindings,
    )


@pytest.mark.parametrize(
    "name,value",
    [("correlation-positive-17", 1), ("correlation-negative-17", -1), ("correlation-zero-20", 0)],
)
def test_independent_rank_anchors(name, value):
    case = CASES[name]
    result = reference(case, case.targets[0])
    assert result.values["rho"] == Decimal(value)
    assert result.values["paired_count"] == len(case.rows)
    assert result.qualified == (value != 0)


def test_average_ties_have_hand_computed_five_sixths():
    case = replace(
        CASES["correlation-positive-17"],
        rows=((1, 1, 3, 3, 3), (1, 2, 3, 3, 3), (2, 2, 3, 3, 3), (3, 3, 3, 3, 3)) * 2,
    )
    result = reference(case, case.targets[0])
    assert abs(result.values["rho"] - Decimal(5) / 6) < Decimal("1e-28")


@pytest.mark.parametrize("size,half", [(17, 8), (53, 26), (105, 52)])
def test_disjoint_means_and_sample_denominators(size, half):
    case = CASES[f"group-{size}"]
    q = case.targets[0]
    result = reference(case, q)
    assert result.qualified
    assert result.values["target_count"] == half
    assert result.values["remainder_count"] == size - half
    outcome = result.values[q.outcome]
    assert (outcome["target"]["mean"], outcome["remainder"]["mean"], outcome["difference"]) == (
        5,
        1,
        4,
    )
    assert outcome["target"]["valid"] == half
    assert result.values["share_total"] == F(half, size)
    condition = replace(q, outcome=q.conditions[0].dimension)
    assert not reference(case, condition).qualified


def test_dedup_counterexample_and_53_known_boundary():
    case = CASES["dedup-20"]
    r = reference(case, case.targets[0])
    assert (
        r.values["target_count"],
        r.values["remainder_count"],
        r.values["ALGORITHMS"]["difference"],
    ) == (10, 10, -4)
    assert r.qualified
    bad = Query("group", conditions=(Predicate("ALGORITHMS", "<=", "1"),), outcome="IMPLEMENTATION")
    assert reference(case, bad).values["IMPLEMENTATION"]["difference"] == F(-1, 5)
    assert not reference(case, bad).qualified
    case = CASES["boundary-53"]
    r = reference(case, case.targets[0])
    assert (r.values["strict_count"], r.values["inclusive_count"], r.values["added_count"]) == (
        4,
        5,
        1,
    )
    # Independent raw Decimal predicate, not another reference/SUT invocation.
    assert sum(row[0] < Decimal("2.75") and row[4] >= 4 for row in case.rows) == 4
    assert sum(row[0] <= Decimal("2.75") and row[4] >= 4 for row in case.rows) == 5


@pytest.mark.parametrize("name,count,n", [("rare-17", 1, 17), ("rare-105", 5, 105)])
def test_rare_fences_anchors(name, count, n):
    case = CASES[name]
    r = reference(case, case.targets[0])
    assert r.qualified
    assert (r.values["q1"], r.values["q3"], r.values["median"], r.values["upper_fence"]) == (
        0,
        0,
        0,
        0,
    )
    assert (r.values["match_count"], r.values["pair_count"]) == (count, n)
    assert len(r.bindings["rare_members"]) == count
    assert len(r.bindings["pair_reference"]) == n


def test_missing_invalid_and_empty_are_not_zero():
    case = CASES["missing-invalid-17"]
    r = reference(case, case.targets[0])
    assert (r.values["paired_count"], r.values["excluded_count"], r.values["state"]) == (
        4,
        13,
        "insufficient_pairs",
    )
    assert not r.qualified
    case = CASES["missing-group-53"]
    r = reference(case, case.targets[0])
    assert (
        r.values["target_count"],
        r.values["remainder_count"],
        r.values["unevaluable_count"],
    ) == (25, 27, 1)
    assert r.values["share_total"] == F(25, 53)
    assert r.values["share_eligible"] == F(25, 52)
    assert score(Decimal("2.75")) == F(11, 4)
    assert score(Decimal("2.751")) is None
    assert score(6) is None and score(True) is None
    case = CASES["constant-scale-0"]
    r = reference(case, case.targets[0])
    assert not r.qualified and r.values["rho"] is None


def test_nonzero_iqr_fence_literal_anchor():
    diffs = (-2, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 2, 4)
    case = replace(
        CASES["rare-17"], rows=tuple((1 + max(d, 0), 1 + max(-d, 0), 3, 3, 3) for d in diffs)
    )
    r = reference(case, case.targets[0])
    assert (r.values["q1"], r.values["q3"], r.values["lower_fence"], r.values["upper_fence"]) == (
        0,
        1,
        F(-3, 2),
        F(5, 2),
    )
    assert r.values["match_count"] == 1


def test_fact_context_sources_values_duplicates_and_no_empty_accuracy():
    case = CASES["scope-attacks-17"]
    claim = claim_for(case)
    run = CandidateRun("unit-run", "completed", ledger=(claim,), selected_ids=(claim.fact_id,))
    good = grade(case, run)
    assert good["coverage"]["value"] == 1 and good["wrong_discoveries"] == 0
    assert (
        grade(case, replace(run, selected_ids=(claim.fact_id,) * 2))["duplicate_discoveries"] == 1
    )
    assert grade(case, replace(run, selected_ids=("invented",)))["unknown_fact_references"] == 1
    for changed in (
        replace(claim, batch_id="foreign"),
        replace(claim, snapshot_id="wrong"),
        replace(claim, run_id="prior"),
    ):
        assert not grade_claim(case, changed, "unit-run")["references_correct"]
    values = copy.deepcopy(claim.values)
    values["population_count"] = 105
    assert (
        grade(case, replace(run, ledger=(replace(claim, values=values),)))["wrong_discoveries"] == 1
    )
    links = dict(claim.bindings)
    links["target.IMPLEMENTATION"] = links["remainder.IMPLEMENTATION"]
    assert not grade_claim(case, replace(claim, bindings=links), "unit-run")["references_correct"]
    empty = CASES["constant-scale-0"]
    result = grade(empty, CandidateRun("unit-run", "completed"))
    assert result["coverage"]["value"] is None
    assert result["reference_accuracy"]["value"] is None


def test_reverse_equivalence_does_not_conflate_condition_outcome_semantics():
    case = CASES["group-53"]
    q = case.targets[0]
    reverse = replace(q, conditions=(Predicate("ALGORITHMS", ">=", "5"),))
    assert reference(case, q).signature == reference(case, reverse).signature
    swapped = Query(
        "group", conditions=(Predicate("IMPLEMENTATION", ">=", "5"),), outcome="ALGORITHMS"
    )
    assert reference(case, q).signature != reference(case, swapped).signature


def test_grader_protocol_denials_are_explicitly_not_real_agent_tests():
    probes = protocol_probes()
    assert len(probes) >= 10 and all(p["passed"] for p in probes)
    assert all(p["scope"] == "grader_contract_only_not_sut_execution" for p in probes)
    assert not trace_request_valid("get_dimension_statistics", {"batch_id": "foreign"}, set())
    assert not trace_request_valid("query_sql", {"path": "teacher-file"}, set())
    assert not trace_request_valid("inspect_discovery_support", {"fact_id": "forged"}, set())


def test_failures_not_hidden_and_agent_never_faked():
    case = CASES["group-17"]

    class Broken:
        def run(self, case, profile):
            raise RuntimeError("must not echo sensitive error")

    report = evaluate(Broken(), [case])
    assert len(report["failures"]) == 2
    assert report["summary"]["C_agent"]["not_run"] == 1
    assert report["summary"]["C_agent"]["input_tokens"] is None
    assert "must not echo" not in json.dumps(report)


def test_frozen_answers_factory_stability_and_no_sut_imports():
    a, b = cases(), cases()
    assert a == b
    assert all(case_digest(x) == case_digest(y) for x, y in zip(a, b, strict=True))
    frozen = json.loads((ROOT / "evals/discovery_v1/oracles.json").read_text(encoding="utf-8"))
    assert frozen["cases"] == [wire(manifest(c)) for c in a]
    for filename in ("reference.py", "cases.py", "contracts.py"):
        tree = ast.parse((ROOT / "evals/discovery_v1" / filename).read_text(encoding="utf-8"))
        names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [
            alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names
        ]
        assert not any(
            name.startswith("app") or "baseline" in name or "qwen" in name for name in names
        )


def test_cli_current_main_reports_unavailable_not_success(tmp_path):
    script = ROOT / "scripts/edu_discovery_eval.py"
    report = tmp_path / "report.json"
    completed = subprocess.run(
        [sys.executable, str(script), "--output", str(report)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert completed.returncode in (0, 1), completed.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["network_model_calls"] == 0
    assert data["summary"]["C_agent"]["executed"] == 0
    assert completed.returncode == (1 if data["failures"] else 0)
    if not data["sut"]["available"]:
        assert data["summary"]["A_default"]["executed"] == 0
        assert data["summary"]["A_default"]["coverage"]["value"] is None


def test_candidate_inputs_do_not_include_oracle_labels_or_targets():
    from dataclasses import asdict

    from evals.discovery_v1.contracts import candidate_input

    for case in cases():
        view = candidate_input(case)
        assert not any(
            hasattr(view, field)
            for field in ("targets", "recipe", "objective", "forbidden", "seed")
        )
        assert view.case_id != case.case_id
        assert case.case_id not in json.dumps(asdict(view), default=str)
        assert view.boundary_requests == tuple(q for q in case.targets if q.kind == "boundary")


def test_independent_and_interaction_target():
    case = CASES["and-interaction-53"]
    r = reference(case, case.targets[0])
    assert r.qualified
    assert (
        r.values["target_count"],
        r.values["remainder_count"],
        r.values["MODELING"]["difference"],
    ) == (13, 40, 4)


def test_rejected_proposals_not_erased_by_safe_empty_presentation():
    case = CASES["group-17"]
    claim = claim_for(case)
    result = grade(
        case,
        CandidateRun(
            "unit-run", "completed", ledger=(claim,), selected_ids=("fabricated",), presented_ids=()
        ),
    )
    assert result["wrong_proposals"] == 1
    assert result["wrong_discoveries"] == 0
    assert result["unknown_fact_references"] == 1
    assert result["coverage"]["count"] == 0


def test_optional_agent_adapter_is_labelled_fake_and_receives_no_oracles():
    class Fake:
        def run(self, source, profile):
            assert not hasattr(source, "targets")
            return CandidateRun(
                "fake",
                "no_valid_findings",
                measurement_origin="scripted_fake",
                input_tokens=123,
                output_tokens=45,
            )

    report = evaluate(Fake(), [CASES["constant-53"]], agent=Fake())
    result = report["cases"][0]["profiles"]["C_agent"]
    assert result["measurement_origin"] == "scripted_fake"
    assert result["input_tokens"] is None and result["output_tokens"] is None
    assert report["network_model_calls"] == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "conditions": [{"dimension": "ALGORITHMS", "operator": "<", "threshold": "2.751"}],
            "outcomes": ["IMPLEMENTATION"],
        },
        {
            "conditions": [{"dimension": "ALGORITHMS", "operator": "<", "threshold": "2.75"}],
            "outcomes": ["ALGORITHMS"],
        },
        {
            "conditions": [{"dimension": "UNKNOWN", "operator": "<", "threshold": "3"}],
            "outcomes": ["MODELING"],
        },
        {"conditions": None, "outcomes": []},
    ],
)
def test_unreasonable_group_requests_rejected(arguments):
    assert not trace_request_valid("compare_student_groups", arguments, set())


def test_cli_requires_pinned_external_revision_and_preserves_existing_report(tmp_path):
    script = ROOT / "scripts/edu_discovery_eval.py"
    output = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(script), "--sut-repo", str(ROOT), "--output", str(output)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 2 and "explicit_sut_requires_expected_sha" in result.stderr
    assert not output.exists()
    output.write_text("preserve-failed-report", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), "--output", str(output)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 2
    assert output.read_text(encoding="utf-8") == "preserve-failed-report"


def test_numeric_strings_wrong_count_types_and_budget_overruns_are_not_accepted():
    case = CASES["correlation-positive-17"]
    claim = claim_for(case)
    values = dict(claim.values)
    values["rho"] = "1"
    assert not grade_claim(case, replace(claim, values=values), "unit-run")["data_correct"]
    values = dict(claim.values)
    values["paired_count"] = F(17)
    assert not grade_claim(case, replace(claim, values=values), "unit-run")["data_correct"]
    trace = ({"tool": "get_dimension_statistics", "arguments": {}, "status": "ok"},) * 6
    outcome = grade(
        case,
        CandidateRun("unit-run", "completed", ledger=(claim,), tool_trace=trace, model_requests=7),
    )
    assert "tool_budget_exceeded" in outcome["errors"]
    assert "model_budget_exceeded" in outcome["errors"]
    assert outcome["repeated_tool_calls"] == 5


def test_invalid_candidate_payload_retains_a_failed_case():
    class Invalid:
        def run(self, source, profile):
            return {"not": "a controlled ledger"}

    report = evaluate(Invalid(), [CASES["group-17"]])
    assert len(report["failures"]) == 2
    assert all(f["result"]["reason"] == "invalid_candidate_payload" for f in report["failures"])


def test_audit_observations_retain_bad_numbers_without_identity_or_text():
    case = CASES["group-17"]
    claim = claim_for(case)
    values = copy.deepcopy(claim.values)
    values[claim.query.outcome]["difference"] = F(99)
    result = grade(
        case,
        CandidateRun(
            "unit-run",
            "completed",
            ledger=(replace(claim, values=values),),
            selected_ids=(claim.fact_id,),
        ),
    )
    assert result["observations"][0]["values"]["difference"] == {"numerator": 99, "denominator": 1}
    text = json.dumps(result)
    assert "synthetic-member-" not in text and "synthetic-record-" not in text
    assert "source_binding_digest" in text


def test_recorded_real_offline_report_is_bound_to_current_oracles_and_evaluator():
    from evals.discovery_v1.reference import digest

    package = ROOT / "evals/discovery_v1"
    report = json.loads((package / "results/2026-10-26-offline.json").read_text(encoding="utf-8"))
    frozen = json.loads((package / "oracles.json").read_text(encoding="utf-8"))
    assert report["oracle_manifest_digest"] == digest(frozen)
    assert report["evaluator_source_digest"] == digest(
        {
            p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(package.glob("*.py"))
        }
    )
    assert report["sut"]["revision"] == "1424655b1cf793542fe7a04de08d46f73772b95c"
    assert report["network_model_calls"] == 0
    assert report["summary"]["C_agent"]["executed"] == 0
    assert report["summary"]["C_agent"]["not_run"] == len(cases())
    assert all(row["profile"] != "C_agent" for row in report["failures"])
    assert any(
        row["case_id"] == "and-interaction-53" and row["profile"] == "A_default"
        for row in report["failures"]
    )
    assert all(p["passed"] for p in report["protocol_probes"])
