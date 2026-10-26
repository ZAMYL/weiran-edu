"""Executable A/B/C acceptance. Real Qwen remains not_run; no network execution switch."""

import argparse
import asyncio
import json
from dataclasses import replace
from fractions import Fraction as F
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.discovery_v1.baseline import Baseline
from evals.discovery_v1.cases import cases
from evals.discovery_v1.contracts import candidate_input
from evals.discovery_v1.grading import grade as frozen_grade
from evals.discovery_v1.grading import grade_claim
from evals.discovery_v1.reference import manifest, wire
from prototypes.adaptive_assessment.synthetic import admit

from .collector import collect
from .grading import grade_native
from .reference import VERSION, Spec, frozen_spec, question_manifest, question_spec, targets
from .safety import evidence_absent_probe, safety_probes

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("A_default", "A_full", "B_http_mock", "C_qwen")
RECIPES = (
    "contest13",
    "contest13_complete",
    "limited",
    "no_evidence",
    "small",
    "privacy20",
    "constant17",
)


def verify_dependencies():
    pins = json.loads(Path(__file__).with_name("dependency_pins.json").read_text(encoding="utf-8"))
    for path, expected in pins["files"].items():
        if sha256((ROOT / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest() != expected:
            raise ValueError("pinned_dependency_changed")
    frozen = json.loads((ROOT / "evals/discovery_v1/oracles.json").read_text(encoding="utf-8"))
    if frozen != {
        "version": "edu-discovery-oracles-v1",
        "cases": [wire(manifest(c)) for c in cases()],
    }:
        raise ValueError("frozen_oracle_drift")
    new_manifest = json.loads(
        Path(__file__).with_name("question_oracles.json").read_text(encoding="utf-8")
    )
    if new_manifest != wire(question_manifest()):
        raise ValueError("question_oracle_drift")
    return pins


def spec_for_recipe(recipe):
    if recipe == "constant17":
        return Spec(
            recipe,
            tuple(f"I{i:02}" for i in range(1, 6)),
            ((F(3),) * 5,) * 17,
            ((F(1), F(5), F(1, 100)),) * 5,
        )
    return question_spec(recipe)


def compatible_recipe(spec):
    # Compare complete items/scales/scores/statuses, not just rows or an oracle label.
    known_five = Spec(
        "five",
        tuple(f"I{i:02}" for i in range(1, 6)),
        ((F(1), F(5), F(3), F(3), F(3)),) * 26 + ((F(5), F(1), F(3), F(3), F(3)),) * 27,
        ((F(1), F(5), F(1, 100)),) * 5,
    )
    if spec.fingerprint == known_five.fingerprint:
        return "five"
    for recipe in RECIPES:
        if spec.fingerprint == spec_for_recipe(recipe).fingerprint:
            return recipe
    return None


def check_admitted_input(spec, source):
    """Local equality bridge before execution. No oracle labels/targets enter the planner."""
    data = source.envelope.data
    if tuple(i.item_id for i in data.items) != spec.items or len(data.participants) != len(
        spec.rows
    ):
        raise ValueError("synthetic_structure_fingerprint_mismatch")
    participants = [p.participant_id for p in data.participants]
    actual = {(o.participant_id, o.item_id): o for o in data.observations}
    for i, pid in enumerate(participants):
        for j, item in enumerate(spec.items):
            o = actual[(pid, item)]
            value = None if o.raw_decimal is None else F(o.raw_decimal)
            if value != spec.rows[i][j]:
                raise ValueError("synthetic_score_fingerprint_mismatch")
    for j, item in enumerate(data.items):
        actual_scale = (
            None
            if item.scale is None
            else (F(item.scale.minimum), F(item.scale.maximum), F(item.scale.step))
        )
        kind = "competency" if item.item_id.startswith("I") else "question"
        unit = "level" if kind == "competency" else "points"
        level = (
            "ordinal" if kind == "competency" else "unknown" if actual_scale is None else "interval"
        )
        if (
            actual_scale != spec.scales[j]
            or (item.rubric_id is not None) != (item.item_id in spec.rubric)
            or item.kind != kind
            or (item.scale is not None and item.scale.unit != unit)
            or dict(source.envelope.material.levels)[item.item_id] != level
        ):
            raise ValueError("synthetic_scale_or_rubric_fingerprint_mismatch")
        for participant in participants:
            observation = actual[(participant, item.item_id)]
            evidence = {e.evidence_id: e for e in data.evidence}
            scores = [evidence[r] for r in observation.evidence_ids if evidence[r].kind == "score"]
            if bool(scores) != (item.item_id not in spec.absent):
                raise ValueError("synthetic_evidence_status_mismatch")
    source.validate()


def not_run(spec, reason, origin=None):
    return {
        "status": "not_run",
        "reason": reason,
        "executed": False,
        "passed": None,
        "coverage": {"count": None, "denominator": len(targets(spec))},
        "actual_tokens": None,
        "actual_cost_cny": None,
        "model_requests": None,
        "measurement_origin": origin,
        "tool_attempts": None,
        "elapsed_seconds": None,
        "audit": None,
    }


def failed_profile(spec, phase, exc):
    result = not_run(spec, "adapter_or_input_validation_failed")
    known = {
        "synthetic_structure_fingerprint_mismatch",
        "synthetic_score_fingerprint_mismatch",
        "synthetic_scale_or_rubric_fingerprint_mismatch",
        "synthetic_evidence_status_mismatch",
    }
    result.update(
        status="failed",
        reason=str(exc) if str(exc) in known else "adapter_or_grading_failed",
        attempted=True,
        executed=phase != "preflight",
        passed=False,
        coverage={"count": 0, "denominator": len(targets(spec))},
        failure_phase=phase,
        error_type=type(exc).__name__,
        false_discoveries=None,
        duplicate_discoveries=None,
        illegal_tool_proposals=None,
        elapsed_seconds=None,
        model_requests=0 if phase == "preflight" else None,
        tool_attempts=0 if phase == "preflight" else None,
    )
    return result


async def native_profile(spec: Spec, recipe: str | None, state_dir: Path, profile: str) -> dict:
    if recipe is None:
        return not_run(spec, "no_matching_full_structure_in_58_allowlist")
    phase = "preflight"
    collected = None
    try:
        check_admitted_input(spec, admit(recipe))
        phase = "execution"
        collected = await collect(recipe, state_dir, profile=profile)
        phase = "grading"
        return {**grade_native(spec, collected), "audit": collected.audit}
    except Exception as exc:
        failed = failed_profile(spec, phase, exc)
        if collected is not None:
            failed.update(
                audit=collected.audit,
                model_requests=collected.audit["model_requests"],
                tool_attempts=collected.run.tool_attempts,
                elapsed_seconds=collected.audit["elapsed_seconds"],
            )
        return failed


def baseline_grade(case, result):
    r = frozen_grade(case, result)
    checked = [grade_claim(case, c, result.run_id) for c in result.ledger]
    return {
        "status": r["status"],
        "executed": True,
        "reason": result.reason,
        "coverage": {k: r["coverage"][k] for k in ("count", "denominator")},
        "computed_coverage": {k: r["computed_coverage"][k] for k in ("count", "denominator")},
        "false_discoveries": r["wrong_discoveries"],
        "duplicate_discoveries": r["duplicate_discoveries"],
        "data_accuracy": r["data_accuracy"],
        "source_accuracy": {
            "count": sum(c["references_correct"] for c in checked),
            "denominator": len(checked),
        },
        "source_binding_accuracy": r["reference_accuracy"],
        "illegal_tool_proposals": r["invalid_tool_requests"],
        "disclosure_errors": [],
        "zero_finding_correct": r["empty_selection"]
        if "forced_discovery" in case.forbidden
        else None,
        "operation_counts": r["operation_counts"],
        "tool_attempts": None,
        "model_requests": 0,
        "elapsed_seconds": r["compute_seconds"],
        "actual_tokens": None,
        "actual_cost_cny": None,
        "passed": not r["errors"]
        and not r["wrong_discoveries"]
        and not r["duplicate_discoveries"]
        and not r["proposed_invalid_facts"]
        and r["coverage"]["count"] == r["coverage"]["denominator"],
        "frozen_grade": r,
        "audit": {
            "run_id": result.run_id,
            "engine_invocations": result.engine_invocations,
            "engine_qualified_count": result.engine_qualified_count,
            "search_attempts": result.search_attempts,
            "selected_ids": result.selected_ids,
            "ledger_claim_count": len(result.ledger),
        },
    }


def summarize(records: list[dict], profile: str) -> dict:
    all_rows = [c["profiles"][profile] for c in records]
    run = [r for r in all_rows if r["executed"]]
    denominator = sum(r["coverage"]["denominator"] for r in all_rows)
    numerator = sum(r["coverage"]["count"] or 0 for r in run)
    accuracy = {}
    for field in ("data_accuracy", "source_accuracy"):
        measured = [r[field] for r in run if field in r]
        total = sum(v["denominator"] for v in measured)
        correct = sum(v["count"] for v in measured)
        accuracy[field] = {
            "count": correct,
            "denominator": total,
            "value": correct / total if total else None,
            "unit": "issued_fact",
        }
    return {
        **accuracy,
        "cases": len(all_rows),
        "executed": len(run),
        "not_run": sum(r["status"] == "not_run" for r in all_rows),
        "failed_before_execution": sum(
            r["status"] == "failed" and not r["executed"] for r in all_rows
        ),
        "passed": sum(r["passed"] is True for r in run),
        "failed": sum(r["passed"] is False for r in all_rows),
        "coverage": {
            "count": numerator if run else None,
            "denominator": denominator,
            "value": numerator / denominator if run and denominator else None,
        },
        "missed_or_unexecuted_targets": denominator - numerator if run else None,
        "pending_targets": denominator
        if not run
        else sum(r["coverage"]["denominator"] for r in all_rows if not r["executed"]),
        "false_discoveries": sum(r["false_discoveries"] for r in run)
        if run and all(r["false_discoveries"] is not None for r in run)
        else None,
        "duplicate_discoveries": sum(r["duplicate_discoveries"] for r in run)
        if run and all(r["duplicate_discoveries"] is not None for r in run)
        else None,
        "illegal_tool_proposals": sum(r["illegal_tool_proposals"] for r in run)
        if run and all(r["illegal_tool_proposals"] is not None for r in run)
        else None,
        "tool_attempts": sum(r["tool_attempts"] or 0 for r in run) if run else None,
        "model_requests": sum(r["model_requests"] for r in run)
        if run and all(r["model_requests"] is not None for r in run)
        else None,
        "elapsed_seconds": sum(r["elapsed_seconds"] for r in run)
        if run and all(r["elapsed_seconds"] is not None for r in run)
        else None,
        "actual_tokens": None,
        "actual_cost_cny": None,
    }


async def build_report() -> dict:
    pins = verify_dependencies()
    baseline = Baseline(ROOT)
    records = []
    suites = [("five_frozen", frozen_spec(case)) for case in cases()]
    suites += [("adaptive_items", spec_for_recipe(recipe)) for recipe in RECIPES]
    unsupported = replace(
        question_spec("contest13_complete"),
        key="all_items_without_evidence",
        absent=tuple(f"Q{i:02}" for i in range(1, 14)),
    )
    suites.append(("adaptive_items", unsupported))
    with TemporaryDirectory(prefix="edu-acceptance-http-mock-") as directory:
        for suite, spec in suites:
            recipe = compatible_recipe(spec)
            profiles = {
                "C_qwen": not_run(spec, "separate_human_authorization_not_granted", "qwen_live")
            }
            if spec.frozen:
                for name, oldprofile in (("A_default", "A_default"), ("A_full", "B_full")):
                    try:
                        profiles[name] = baseline_grade(
                            spec.frozen, baseline.run(candidate_input(spec.frozen), oldprofile)
                        )
                    except Exception as exc:
                        profiles[name] = failed_profile(spec, "execution", exc)
            else:
                profiles["A_full"] = not_run(
                    spec, "budget50_is_specific_to_five_dimension_discover"
                )
                profiles["A_default"] = await native_profile(spec, recipe, Path(directory), "fixed")
            if not (spec.frozen and spec.frozen.distractors):
                profiles["B_http_mock"] = await native_profile(
                    spec, recipe, Path(directory), "http_mock"
                )
            else:
                profiles["B_http_mock"] = not_run(
                    spec, "distractor_batch_requires_unimplemented_native_provider"
                )
            records.append(
                {
                    "case_id": spec.key,
                    "suite": suite,
                    "seed": spec.seed,
                    "input_fingerprint": spec.fingerprint,
                    "recognized_items": list(spec.items),
                    "participant_count": len(spec.rows),
                    "target_count": len(targets(spec)),
                    "profiles": profiles,
                }
            )
    with TemporaryDirectory(prefix="edu-acceptance-safety-") as safety_dir:
        probes = await safety_probes(Path(safety_dir))
        degraded = await evidence_absent_probe(Path(safety_dir))
    summary = {
        suite: {p: summarize([r for r in records if r["suite"] == suite], p) for p in PROFILES}
        for suite in ("five_frozen", "adaptive_items")
    }
    return {
        "safety_probes": probes,
        "degradation_probes": [degraded],
        "schema_version": VERSION,
        "synthetic_only": True,
        "paid_api_calls": 0,
        "dependency_pins": pins,
        "sut": baseline.metadata,
        "summary": summary,
        "cases": records,
        "real_qwen_status": "not_run",
        "model_quality_uplift": "not_proven",
        "historical_frozen_baseline": {
            "A_default": "11/12",
            "B_full": "12/12",
            "omission_to_preserve": "and-interaction-53",
        },
        "limitations": [
            "all profiles retain every planned case and target denominator",
            "Mock usage/price are fixtures, not actual tokens or bills",
            "five frozen Claim and native partial-outcome contracts scored separately",
            "13-question fixed tool schedule is not the five-dimensional discover() engine",
            "withheld/real teacher authentication/Production are outside this synthetic prototype",
            "local source replay is not a cryptographic attestation against a malicious adapter",
        ],
    }


def markdown(report):
    lines = [
        "# 自适应诊断独立离线效果与安全验收",
        "",
        "真实 Qwen：**not_run**；付费请求0；Mock不代表模型质量。",
        "",
        "| Suite | Profile | Executed / All | Not run | Full target coverage | False | Failed |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for suite, profiles in report["summary"].items():
        for name, r in profiles.items():
            c = r["coverage"]
            lines.append(
                f"| {suite} | {name} | {r['executed']}/{r['cases']} | {r['not_run']} | "
                f"{c['count'] if c['count'] is not None else 'not_run'}/{c['denominator']} | "
                f"{r['false_discoveries']} | {r['failed']} |"
            )
    lines += [
        "",
        "## 每例完整结果",
        "",
        "| Case | A_default | A_full | B_http_mock | C_qwen |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in report["cases"]:
        cells = []
        for profile in PROFILES:
            r = c["profiles"][profile]
            cells.append(
                "not_run: " + r["reason"]
                if not r["executed"]
                else (
                    f"{r['coverage']['count']}/{r['coverage']['denominator']} "
                    f"{'pass' if r['passed'] else 'failed'}"
                )
            )
        lines.append("| " + c["case_id"] + " | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 失败与局限",
        "",
        *["- " + v for v in report["limitations"]],
        "",
        "原默认预算 AND 漏检必须保留；覆盖缺口、未执行和最终选择遗漏均不是可删除样本。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    for path in (args.output, args.markdown):
        if path is not None and path.exists():
            parser.error("new_output_files_required_preserve_failures")
    if args.output.suffix.lower() != ".json" or (
        args.markdown and args.markdown.suffix.lower() != ".md"
    ):
        parser.error("JSON_and_Markdown_suffixes_required")
    report = asyncio.run(build_report())
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(wire(report), ensure_ascii=False, indent=2))
    if args.markdown:
        with args.markdown.open("x", encoding="utf-8") as stream:
            stream.write(markdown(report))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    # Failed/omitted findings remain visible, independently of successful program execution.
    return (
        1
        if any(not p["safe_blocked"] for p in report["safety_probes"])
        or any(not p["safe_degradation"] for p in report["degradation_probes"])
        or any(r["passed"] is False for c in report["cases"] for r in c["profiles"].values())
        else 0
    )
