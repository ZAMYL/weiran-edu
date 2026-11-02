"""Independent anchors, actual #58 protocol paths, tamper and disclosure grading."""

import asyncio
import copy
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction as F

import pytest
from evals.adaptive_acceptance.collector import collect
from evals.adaptive_acceptance.grading import grade_native, source_check
from evals.adaptive_acceptance.reference import (
    expectation,
    frozen_spec,
    group,
    question_manifest,
    question_spec,
    relation,
    targets,
)
from evals.adaptive_acceptance.runner import (
    ROOT,
    check_admitted_input,
    compatible_recipe,
    main,
    not_run,
    spec_for_recipe,
    summarize,
    verify_dependencies,
)
from evals.adaptive_acceptance.safety import safety_probes
from evals.discovery_v1.cases import cases
from evals.discovery_v1.reference import reference, wire
from prototypes.adaptive_assessment.live.pricing import PRICE
from prototypes.adaptive_assessment.synthetic import admit


@pytest.fixture(scope="module")
def captured(tmp_path_factory):
    root = tmp_path_factory.mktemp("adaptive-qwen-eval")
    result = {}

    async def execute():
        for recipe in (
            "five",
            "contest13",
            "contest13_complete",
            "limited",
            "no_evidence",
            "small",
            "privacy20",
            "constant17",
        ):
            result[recipe] = await collect(recipe, root)

    asyncio.run(execute())
    return result


def five_spec():
    return frozen_spec(next(c for c in cases() if c.case_id == "group-53"))


def test_frozen_dependency_bytes_and_question_manifest_are_unchanged():
    assert verify_dependencies()["dependency_commits"]["PR47"].startswith("9146312")
    assert json.loads(
        (ROOT / "evals/adaptive_acceptance/question_oracles.json").read_text()
    ) == wire(question_manifest())


def test_manual_question_anchors():
    s = question_spec("contest13")
    assert len(s.items) == 13 and len(s.rows) == 20
    assert s.stats("Q01")["mean"] == 50 and s.stats("Q01")["median"] == 50
    assert s.stats("Q13")["valid_count"] == 15
    assert s.stats("Q13")["states"] == {"valid": 15, "missing": 5}
    assert s.stats("Q13")["mean"] == 60 and s.stats("Q13")["median"] == 80
    assert question_spec("contest13_complete").stats("Q13")["mean"] == 50
    v, links, qualified = group(s, [{"item_id": "Q01", "operator": "<=", "threshold": "20"}], "Q02")
    assert (v["target_count"], v["remainder_count"], v["denominator"]) == (10, 10, 20)
    assert (v["target_mean"], v["remainder_mean"], v["difference"]) == (80, 20, 60)
    assert qualified and links["target.valid"][1] == tuple(range(10, 20))
    v, links, qualified = relation(s, "Q01", "Q02")
    assert v["rho"] == Decimal(-1) and v["paired_count"] == 20 and qualified


def test_manual_missing_scale_evidence_and_minimum_sample():
    s = question_spec("limited")
    assert s.stats("Q11")["valid_count"] == 0
    assert s.stats("Q11")["states"] == {"evidence_absent": 20}
    assert s.stats("Q12")["mean"] == 50 and not s.stats("Q12")["range_checked"]
    with pytest.raises(ValueError):
        relation(s, "Q01", "Q12")
    with pytest.raises(ValueError):
        group(s, [{"item_id": "Q12", "operator": "<", "threshold": "20"}], "Q01")
    assert relation(question_spec("small"), "Q01", "Q02")[0]["rho"] is None
    assert question_spec("no_evidence").absent == ("Q01",)
    assert question_spec("small").stats("Q01")["mean"] == 40
    assert question_spec("small").stats("Q02")["mean"] == 60
    assert question_spec("small").stats("Q13")["states"] == {"valid": 3}


def test_independent_precision_boundary_and_nonzero_missing_denominator():
    s = question_spec("contest13_complete")
    rows = ((F(275, 100), F(80)), (F(274, 100), F(20)), (None, F(20)), (F(2751, 1000), F(80)))
    s = replace(s, items=("Q01", "Q02"), rows=rows, scales=((F(0), F(100), F(1, 100)),) * 2)
    inclusive = group(s, [{"item_id": "Q01", "operator": "<=", "threshold": "2.75"}], "Q02")[0]
    strict = group(s, [{"item_id": "Q01", "operator": "<", "threshold": "2.75"}], "Q02")[0]
    assert inclusive["target_count"] == 2 and strict["target_count"] == 1
    assert inclusive["eligible_denominator"] == 2 and inclusive["denominator"] == 4
    assert inclusive["share_total"] == F(1, 2) and inclusive["share_eligible"] == 1
    assert inclusive["unevaluable_count"] == 2


def test_five_frozen_anchor_is_not_recomputed_by_agent():
    s = five_spec()
    q = s.frozen.targets[0]
    old = reference(s.frozen, q)
    assert old.values["target_count"] == 26 and old.values["remainder_count"] == 27
    assert old.values["IMPLEMENTATION"]["difference"] == 4
    assert s.stats("I01")["mean"] == F(161, 53)
    assert len(targets(s)) == 1


@pytest.mark.parametrize(
    "recipe",
    [
        "five",
        "contest13",
        "contest13_complete",
        "limited",
        "no_evidence",
        "small",
        "privacy20",
        "constant17",
    ],
)
def test_actual_http_mock_facts_and_sources_independently_grade(captured, recipe):
    c = captured[recipe]
    spec = five_spec() if recipe == "five" else spec_for_recipe(recipe)
    g = grade_native(spec, c)
    assert g["data_accuracy"]["count"] == g["data_accuracy"]["denominator"]
    assert g["source_accuracy"]["count"] == g["source_accuracy"]["denominator"]
    assert not g["false_discoveries"] and not g["duplicate_discoveries"]
    assert not g["disclosure_errors"]
    assert g["actual_tokens"] is None and g["actual_cost_cny"] is None
    assert c.audit["budget"]["origin"] == "http_mock" and c.audit["paid_api_calls"] == 0
    assert c.audit["planner_class"].endswith(".QwenAdaptivePlanner")
    assert c.run.model_requests <= 6 and c.run.tool_attempts <= 5


def test_full_target_denominators_and_misses_are_retained(captured):
    a = grade_native(question_spec("contest13"), captured["contest13"])
    b = grade_native(question_spec("limited"), captured["limited"])
    assert a["coverage"] == {"count": 2, "denominator": 3}
    assert b["coverage"] == {"count": 2, "denominator": 5}
    assert not a["passed"] and not b["passed"]
    assert len(a["missed_targets"]) == 1 and len(b["missed_targets"]) == 3


def test_data_change_changes_actual_next_decision(captured):
    missing = [x["tool"] for x in captured["contest13"].audit["planner_choices"]]
    complete = [x["tool"] for x in captured["contest13_complete"].audit["planner_choices"]]
    assert missing[1] == "inspect_item_quality"
    assert complete[1] == "query_dimension_relation"
    assert (
        captured["contest13"].audit["source_fingerprint"]
        != captured["contest13_complete"].audit["source_fingerprint"]
    )


def test_planner_outbound_transcript_has_no_oracle_or_private_rows(captured):
    for _name, c in captured.items():
        for request in c.audit["http_transcript"]:
            raw = json.dumps(request)
            for forbidden in (
                '"case_id"',
                '"seed"',
                '"oracle"',
                '"targets"',
                '"rows"',
                '"participant_id"',
                '"evidence_id"',
                '"source_fingerprint"',
                "answers/",
            ):
                assert forbidden not in raw
            assert request["protocol"]["enable_thinking"] is False
            assert request["protocol"]["max_tokens"] == 800
            assert request["protocol"]["parallel_tool_calls"] is False
    for recipe in ("contest13", "contest13_complete"):
        first = captured[recipe].audit["http_transcript"][0]
        schema = json.dumps(first["tool_schemas"])
        assert "Q13" in schema and "I01" not in schema


def test_zero_discovery_is_not_a_hallucinated_pass(captured):
    for name in ("constant17", "small"):
        result = grade_native(spec_for_recipe(name), captured[name])
        assert result["zero_finding_correct"] is True and result["passed"]
        assert not captured[name].run.selected_ids


def test_cumulative_disclosure_suppresses_one_record_difference(captured):
    f = captured["privacy20"].run.facts[0]
    values = {i["item_id"]: i for i in f.view["items"]}
    assert values["Q01"]["valid_count"] == 15
    assert values["Q02"] == {"item_id": "Q02", "state": "privacy_or_data_limited"}
    g = grade_native(question_spec("privacy20"), captured["privacy20"])
    assert g["coverage"] == {"count": 0, "denominator": 1}
    assert not g["disclosure_errors"]


def test_independent_summary_grader_detects_forgery_even_with_updated_runtime_hash(captured):
    c = copy.deepcopy(captured["five"])
    first = c.run.facts[0]
    view = copy.deepcopy(first.view)
    view["items"][0]["mean"] = {"numerator": 999, "denominator": 1, "display": "999.0000"}
    new = replace(
        first,
        view=view,
        view_sha256=hashlib.sha256(
            json.dumps(view, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest(),
    )
    c.executor.facts[new.fact_id] = new
    c.run = replace(c.run, facts=(new, *c.run.facts[1:]))
    c.executor._verify(new)  # Hash-only summary replay is not the independent oracle.
    g = grade_native(five_spec(), c)
    assert not g["fact_checks"][0]["data_correct"] and not g["passed"]


def test_wrong_record_source_cannot_be_credited(captured):
    c = captured["contest13"]
    fact = next(f for f in c.run.facts if f.tool == "compare_student_groups")
    expected, sources, _ = expectation(
        question_spec("contest13"), fact.tool, fact.query["arguments"]
    )
    first, second = fact.item_sources[:2]
    wrong = replace(first, participants=second.participants, references=second.references)
    tampered = replace(fact, item_sources=(wrong, *fact.item_sources[1:]))
    okay, errors, _ = source_check(question_spec("contest13"), c.executor, tampered, sources)
    assert not okay and "actual_participants_mismatch" in errors


def test_unknown_final_and_cross_snapshot_are_not_trusted(captured):
    c = copy.deepcopy(captured["five"])
    c.run = replace(c.run, selected_ids=("invented",))
    assert grade_native(five_spec(), c)["false_discoveries"] == 1
    c = copy.deepcopy(captured["five"])
    f = c.run.facts[1]
    bad = replace(f, snapshot_id="foreign")
    c.run = replace(c.run, facts=(c.run.facts[0], bad, *c.run.facts[2:]))
    c.executor.facts[bad.fact_id] = bad
    assert not grade_native(five_spec(), c)["fact_checks"][1]["data_correct"]


def test_no_allowlist_extension_just_to_improve_coverage():
    mapped = [c.case_id for c in cases() if compatible_recipe(frozen_spec(c))]
    assert mapped == ["group-53"]
    # Empty score rows do not make 13 items equivalent to five dimensions.
    assert (
        compatible_recipe(frozen_spec(next(c for c in cases() if c.case_id == "constant-scale-0")))
        is None
    )
    wrong = replace(question_spec("contest13_complete"), rows=question_spec("contest13").rows)
    with pytest.raises(ValueError, match="score_fingerprint"):
        check_admitted_input(wrong, admit("contest13_complete"))


def test_global_denominator_never_shrinks_to_executed_subset(captured):
    rows = []
    for case in cases():
        spec = frozen_spec(case)
        result = (
            grade_native(spec, captured["five"])
            if case.case_id == "group-53"
            else not_run(spec, "unsupported")
        )
        rows.append(
            {"profiles": {"B_http_mock": result, "C_qwen": not_run(spec, "no_authorization")}}
        )
    b = summarize(rows, "B_http_mock")
    c = summarize(rows, "C_qwen")
    assert (b["executed"], b["not_run"], b["cases"]) == (1, 18, 19)
    assert b["coverage"] == {"count": 1, "denominator": 12, "value": 1 / 12}
    assert c["coverage"] == {"count": None, "denominator": 12, "value": None}
    assert c["actual_cost_cny"] is None and c["actual_tokens"] is None


def test_price_reservation_is_not_lowered_and_real_template_is_unexecuted():
    assert PRICE.max_input_tokens == 991808
    assert PRICE.cost(991808, 800) == Decimal("0.7956064")
    record = json.loads((ROOT / "evals/adaptive_acceptance/live_record.template.json").read_text())
    assert record["status"] == "not_run" and record["budget"] is None
    assert all(x is None for x in record["measurements"].values())


def test_executed_security_probes_and_budget_failures(tmp_path):
    probes = asyncio.run(safety_probes(tmp_path))
    assert len(probes) == 11 and all(p["safe_blocked"] for p in probes)
    by_name = {p["probe"]: p for p in probes}
    for name in ("small_token_cap", "small_cny_cap"):
        assert not by_name[name]["audit"]["http_transcript"]
        assert by_name[name]["audit"]["budget"]["request_count"] == 0
    assert by_name["forged_final_fact"]["grade"]["wrong_final_proposals"] == 1
    assert by_name["duplicate_final_fact"]["grade"]["duplicate_final_proposals"] == 1
    assert by_name["missing_usage"]["audit"]["budget"]["charged_tokens"] == 992608
    assert by_name["one_request_cap"]["audit"]["budget"]["request_count"] == 1


def test_cli_forbids_live_and_existing_output(tmp_path):
    path = tmp_path / "existing.json"
    path.write_text("preserve")
    with pytest.raises(SystemExit):
        main(["--output", str(path)])
    assert path.read_text() == "preserve"
    with pytest.raises(SystemExit):
        main(["--output", str(tmp_path / "new.json"), "--execute"])
    with pytest.raises(ValueError, match="real_calls_disabled"):
        asyncio.run(collect("five", tmp_path, profile="qwen_live"))


def test_reference_has_no_runtime_or_agent_imports():
    import ast

    path = ROOT / "evals/adaptive_acceptance/reference.py"
    imports = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        if isinstance(node, ast.Import):
            imports += [n.name for n in node.names]
    assert not any(i.startswith(("app", "prototypes", "httpx")) for i in imports)


def test_eval_ruff_and_format_in_existing_backend_ci():
    paths = [
        str(ROOT / "evals/adaptive_acceptance"),
        str(ROOT / "scripts/edu_adaptive_acceptance.py"),
    ]
    for args in (("check",), ("format", "--check")):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                *args,
                "--config",
                str(ROOT / "apps/api/pyproject.toml"),
                *paths,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_every_registered_input_is_independently_matched():
    for recipe in (
        "contest13",
        "contest13_complete",
        "limited",
        "no_evidence",
        "small",
        "privacy20",
        "constant17",
    ):
        check_admitted_input(spec_for_recipe(recipe), admit(recipe))


def test_preflight_failure_is_retained_without_running_the_model(tmp_path):
    from evals.adaptive_acceptance.runner import native_profile

    spec = question_spec("contest13")
    result = asyncio.run(native_profile(spec, "contest13_complete", tmp_path, "http_mock"))
    assert result["status"] == "failed" and result["failure_phase"] == "preflight"
    assert result["coverage"]["denominator"] == 3 and result["passed"] is False
    assert result["audit"] is None and not result["executed"]


def test_final_card_cannot_invent_numbers_after_valid_tool_execution(captured):
    c = copy.deepcopy(captured["five"])
    cards = list(c.run.cards)
    card = copy.deepcopy(cards[0])
    card["metrics"]["paired_count"] = 999
    cards[0] = card
    c.run = replace(c.run, cards=tuple(cards))
    g = grade_native(five_spec(), c)
    assert g["false_discoveries"] == 1
    assert "unverified_card_metrics_or_context" in g["card_projection_errors"]
    assert not g["passed"]


def test_pins_are_git_canonical_across_windows_and_linux():
    pins = verify_dependencies()
    for path, expected in pins["files"].items():
        raw = (ROOT / path).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == expected
        assert (
            hashlib.sha256(raw.replace(b"\n", b"\r\n").replace(b"\r\n", b"\n")).hexdigest()
            == expected
        )


def test_no_score_evidence_scope_produces_no_numeric_discovery(tmp_path):
    from evals.adaptive_acceptance.safety import evidence_absent_probe

    result = asyncio.run(evidence_absent_probe(tmp_path))
    assert result["safe_degradation"]
    assert [t["tool"] for t in result["audit"]["tool_ledger"]] == [
        "get_dimension_statistics",
        "inspect_item_quality",
    ]
    assert result["audit"]["issued_facts"][0]["items"][0]["valid_count"] == 0
    assert all(card["type"] == "item_quality_review" for card in result["audit"]["cards"])


def test_duplicate_ledger_records_stay_in_accuracy_denominator(captured):
    c = copy.deepcopy(captured["five"])
    c.run = replace(c.run, facts=(*c.run.facts, c.run.facts[0]))
    g = grade_native(five_spec(), c)
    assert g["data_accuracy"] == {"count": 3, "denominator": 5}
    assert g["source_accuracy"] == {"count": 3, "denominator": 5}
    assert not g["passed"]
    assert "ambiguous_ledger_fact_id" in g["fact_checks"][0]["errors"]
