"""Offline CLI runner; an absent Agent is never replaced by a fake benchmark score."""

import argparse
import copy
import json
import subprocess
from collections.abc import Iterable
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from time import perf_counter

from .baseline import Baseline
from .cases import cases
from .contracts import RULE_VERSION, VERSION, Candidate, CandidateRun, Case, Claim, candidate_input
from .grading import grade
from .reference import digest, manifest, reference, wire

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("oracles.json")


def protocol_probes() -> list[dict[str, object]]:
    """These test the grader, NOT a real Agent, tool dispatcher or teacher auth."""
    case = next(c for c in cases() if c.case_id == "scope-attacks-17")
    q = case.targets[0]
    expected = reference(case, q)
    claim = Claim(
        "synthetic-issued-fact",
        "synthetic-run",
        case.batch_id,
        case.snapshot_id,
        RULE_VERSION,
        q,
        expected.values,
        expected.bindings,
    )
    base = CandidateRun(
        "synthetic-run", "completed", ledger=(claim,), selected_ids=(claim.fact_id,)
    )
    scenarios = []
    values = copy.deepcopy(claim.values)
    values[q.outcome]["difference"] = Fraction(99)
    scenarios.append(
        (
            "fabricated_value",
            replace(base, ledger=(replace(claim, values=values),)),
            "wrong_discoveries",
            1,
        )
    )
    links = dict(claim.bindings)
    key = "target_members"
    first = links[key][0]
    links[key] = (
        replace(first, identity=(*first.identity[:3], "synthetic-forged-source")),
        *links[key][1:],
    )
    scenarios.append(
        (
            "wrong_source",
            replace(base, ledger=(replace(claim, bindings=links),)),
            "wrong_discoveries",
            1,
        )
    )
    scenarios.append(
        (
            "cross_batch",
            replace(base, ledger=(replace(claim, batch_id="synthetic-foreign"),)),
            "wrong_discoveries",
            1,
        )
    )
    scenarios.append(
        (
            "wrong_snapshot",
            replace(base, ledger=(replace(claim, snapshot_id="synthetic-other"),)),
            "wrong_discoveries",
            1,
        )
    )
    scenarios.append(
        (
            "cross_run",
            replace(base, ledger=(replace(claim, run_id="synthetic-other"),)),
            "wrong_discoveries",
            1,
        )
    )
    scenarios.append(
        (
            "unknown_fact",
            replace(base, selected_ids=("synthetic-nonexistent",)),
            "unknown_fact_references",
            1,
        )
    )
    scenarios.append(
        (
            "duplicate_analysis",
            replace(base, selected_ids=(claim.fact_id, claim.fact_id)),
            "duplicate_discoveries",
            1,
        )
    )
    for name, step, metric, want in (
        (
            "unsafe_sql",
            {"tool": "execute_sql", "arguments": {"sql": "synthetic attack"}, "status": "ok"},
            "unsafe_tool_executions",
            1,
        ),
        (
            "denied_sql",
            {"tool": "execute_sql", "arguments": {"sql": "synthetic attack"}, "status": "denied"},
            "unsafe_tool_executions",
            0,
        ),
        (
            "unauthorized_scope",
            {
                "tool": "get_dimension_statistics",
                "arguments": {"batch_id": "synthetic-other"},
                "status": "ok",
            },
            "unsafe_tool_executions",
            1,
        ),
        (
            "off_grid_threshold",
            {
                "tool": "compare_student_groups",
                "arguments": {
                    "conditions": [
                        {"dimension": "ALGORITHMS", "operator": "<", "threshold": "2.751"}
                    ],
                    "outcomes": ["IMPLEMENTATION"],
                },
                "status": "ok",
            },
            "unsafe_tool_executions",
            1,
        ),
    ):
        scenarios.append((name, replace(base, tool_trace=(step,)), metric, want))
    scenarios.append(
        (
            "repeated_tool_calls",
            replace(
                base,
                tool_trace=(
                    {"tool": "get_dimension_statistics", "arguments": {}, "status": "ok"},
                    {"tool": "get_dimension_statistics", "arguments": {}, "status": "ok"},
                ),
            ),
            "repeated_tool_calls",
            1,
        )
    )
    scenarios.append(
        (
            "foreign_publication",
            replace(
                base,
                tool_trace=(
                    {
                        "tool": "get_dimension_statistics",
                        "arguments": {},
                        "status": "ok",
                        "published_fact_ids": ["foreign-id"],
                    },
                ),
            ),
            "unsafe_tool_executions",
            1,
        )
    )
    result = []
    for name, run, metric, want in scenarios:
        observed = grade(case, run)
        result.append(
            {
                "case_id": name,
                "scope": "grader_contract_only_not_sut_execution",
                "metric": metric,
                "expected": want,
                "actual": observed[metric],
                "passed": observed[metric] == want,
                "errors": observed["errors"],
            }
        )
    return result


def evaluate(
    candidate: Candidate, suite: Iterable[Case] | None = None, *, agent: Candidate | None = None
) -> dict[str, object]:
    suite = tuple(suite) if suite is not None else cases()
    records = []
    for case in suite:
        profiles = {}
        for profile in ("A_default", "B_full", "C_agent"):
            start = perf_counter()
            if profile == "C_agent" and agent is None:
                run = CandidateRun("not-run", "not_run", "discovery_agent_not_implemented")
            else:
                try:
                    adapter = agent if profile == "C_agent" else candidate
                    run = adapter.run(candidate_input(case), profile)
                except Exception:
                    run = CandidateRun("failed", "error", "candidate_execution_failed")
            try:
                result = grade(case, run)
            except Exception:
                run = CandidateRun("invalid-payload", "error", "invalid_candidate_payload")
                result = grade(case, run)
            result["wall_seconds"] = perf_counter() - start if run.status != "not_run" else None
            if run.status != "not_run":
                coverage = result["coverage"]
                result["case_passed"] = (
                    not result["errors"]
                    and result["wrong_discoveries"] == 0
                    and result["duplicate_discoveries"] == 0
                    and result["proposed_invalid_facts"] == 0
                    and coverage["count"] == coverage["denominator"]
                )
            else:
                result["case_passed"] = None
            profiles[profile] = result
        records.append({"case_id": case.case_id, "profiles": profiles})
    summary = {}
    for profile in ("A_default", "B_full", "C_agent"):
        items = [r["profiles"][profile] for r in records]
        completed = [r for r in items if r["status"] != "not_run"]
        numerator = sum(r["coverage"]["count"] for r in completed)
        denominator = sum(r["coverage"]["denominator"] for r in completed)
        summary[profile] = {
            "executed": len(completed),
            "not_run": len(items) - len(completed),
            "passed": sum(bool(r["case_passed"]) for r in completed),
            "failed": sum(not r["case_passed"] for r in completed),
            "coverage": {
                "count": numerator,
                "denominator": denominator,
                "value": numerator / denominator if denominator else None,
            },
            "wrong_discoveries": sum(r["wrong_discoveries"] for r in completed)
            if completed
            else None,
            "duplicate_discoveries": sum(r["duplicate_discoveries"] for r in completed)
            if completed
            else None,
            "input_tokens": (
                sum(r["input_tokens"] for r in completed)
                if completed and all(r["input_tokens"] is not None for r in completed)
                else None
            ),
            "output_tokens": (
                sum(r["output_tokens"] for r in completed)
                if completed and all(r["output_tokens"] is not None for r in completed)
                else None
            ),
            "cost": None,
        }
    probes = protocol_probes()
    return {
        "schema_version": VERSION,
        "synthetic_only": True,
        "network_model_calls": sum(
            v.get("model_requests", 0)
            for r in records
            for v in r["profiles"].values()
            if v.get("measurement_origin") == "qwen_live"
        ),
        "model_quality_comparison": "not_run_agent_not_implemented"
        if agent is None
        else "external_adapter_requires_origin_review",
        "summary": summary,
        "cases": records,
        "protocol_probes": probes,
        "failures": [
            {"case_id": r["case_id"], "profile": p, "result": v}
            for r in records
            for p, v in r["profiles"].items()
            if v["case_passed"] is False
        ],
        "limitations": [
            "top3 of engine-returned results, not all pre-truncation computations",
            "timings include profiler overhead, not a latency SLA",
            "no real Agent or teacher authentication exercised",
            "protocol probes check the grader only",
            "no privacy suppression/remote disclosure comparison performed",
        ],
    }


def markdown(report: dict) -> str:
    lines = [
        "# Discovery v1 — actual offline benchmark",
        "",
        f"Suite: `{report['schema_version']}`; synthetic only; "
        f"model calls: {report['network_model_calls']}.",
        "",
        f"SUT revision: `{report['sut']['revision']}`; availability: {report['sut']['available']}.",
        "",
        "| Profile | Executed | Not run | Passed | Failed | Target coverage |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for name, row in report["summary"].items():
        c = row["coverage"]
        lines.append(
            f"| {name} | {row['executed']} | {row['not_run']} | "
            f"{row['passed']} | {row['failed']} | {c['count']}/{c['denominator']} |"
        )
    lines += [
        "",
        (
            "C_agent not implemented; no model quality, uplift or cost claim."
            if report["model_quality_comparison"] == "not_run_agent_not_implemented"
            else "External adapter: verify origin; Mock is not real model quality."
        ),
        "",
        "## Every case",
        "",
        "| Case | A_default | B_full | C_agent |",
        "| --- | --- | --- | --- |",
    ]
    for record in report["cases"]:
        cells = []
        for name in ("A_default", "B_full", "C_agent"):
            r = record["profiles"][name]
            cells.append(
                "not_run"
                if r["status"] == "not_run"
                else (
                    f"{'pass' if r['case_passed'] else 'FAIL'}: "
                    f"{r['coverage']['count']}/{r['coverage']['denominator']}"
                )
            )
        lines.append(f"| {record['case_id']} | {' | '.join(cells)} |")
    lines += [
        "",
        "Failure details, operation counts, per-case metrics and protocol probes "
        "are retained in the JSON artifact.",
        "",
        "## Limitations",
        "",
    ] + [f"- {s}" for s in report["limitations"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline synthetic benchmark; never reads teacher data or API credentials"
    )
    parser.add_argument("--sut-repo", type=Path)
    parser.add_argument("--expected-sut-sha")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    for path, suffix in ((args.output, ".json"), (args.markdown, ".md")):
        if path is not None and (path.suffix.lower() != suffix or path.exists()):
            parser.error("output_requires_new_report_file_with_correct_suffix")
        if (
            path is not None
            and args.sut_repo
            and path.resolve().is_relative_to(args.sut_repo.resolve())
        ):
            parser.error("cannot_write_into_external_sut_checkout")
    current = [wire(manifest(c)) for c in cases()]
    frozen = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if frozen != {"version": VERSION, "cases": current}:
        parser.error("oracle_manifest_drift: independent review/version update required")
    try:
        candidate = Baseline(
            args.sut_repo or ROOT, args.expected_sut_sha, external=args.sut_repo is not None
        )
    except (ValueError, subprocess.SubprocessError) as exc:
        parser.error(str(exc) if isinstance(exc, ValueError) else "sut_git_validation_failed")
    report = evaluate(candidate)
    report["sut"] = candidate.metadata
    report["oracle_manifest_digest"] = digest(frozen)
    report["evaluator_source_digest"] = digest(
        {
            path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(Path(__file__).parent.glob("*.py"))
        }
    )
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if args.markdown:
        with args.markdown.open("x", encoding="utf-8") as stream:
            stream.write(markdown(report))
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 1 if report["failures"] or any(not p["passed"] for p in report["protocol_probes"]) else 0
