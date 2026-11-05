"""Execute immutable #72 Windows tests in file shards and verify complete coverage."""

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter

from plan_windows_pytest_shards import GROUPS, plan

ROOT = Path(__file__).resolve().parents[1]
BASE = "bda5d37955199f392154b370d335eda630099e75"
PROTECTED = ["apps/api", "apps/web", "prototypes", "evals", "docs", ".github/workflows/ci.yml"]


def revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def run_pytest(arguments, audit):
    env = dict(os.environ)
    env.update(
        PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / "apps/api"))),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONUTF8="1",
        EDU_SHARD_AUDIT=str(audit),
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "scripts.windows_shard_audit", *arguments],
        cwd=ROOT / "apps/api",
        env=env,
        check=False,
    ).returncode


def execute(name):
    started = perf_counter()
    subprocess.run(["git", "diff", "--exit-code", BASE, "--", *PROTECTED], cwd=ROOT, check=True)
    folder = ROOT / "artifacts" / name
    folder.mkdir(parents=True, exist_ok=True)
    collected = folder / "collection.json"
    if run_pytest(["tests", "--collect-only", "-q", "-p", "no:cacheprovider"], collected):
        raise ValueError("collection_failed")
    suite = json.loads(collected.read_text(encoding="utf-8"))["nodes"]
    manifest = {"source_sha": revision(), "nodes": suite}
    shard = plan(manifest)["groups"][name]
    audit = folder / "execution.json"
    code = run_pytest(
        ["-q", "-ra", "--durations=30", "--junitxml=" + str(folder / "junit.xml"), *shard["files"]],
        audit,
    )
    execution = json.loads(audit.read_text(encoding="utf-8")) if audit.exists() else None
    record = {
        "source_sha": revision(),
        "test_baseline_sha": BASE,
        "shard": name,
        "suite": suite,
        "expected": shard["node_ids"],
        "execution": execution,
        "pytest_exit_code": code,
        "elapsed_seconds": perf_counter() - started,
    }
    (folder / "result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return code


def verify(folder, expected_revision):
    records = [json.loads(p.read_text(encoding="utf-8")) for p in folder.rglob("result.json")]
    if Counter(r["shard"] for r in records) != Counter(GROUPS.keys()):
        raise ValueError("missing_or_duplicate_shards")
    suite = records[0]["suite"]
    all_expected = []
    summary = []
    for record in records:
        if (
            record["source_sha"] != expected_revision
            or record["test_baseline_sha"] != BASE
            or record["suite"] != suite
            or record["pytest_exit_code"] != 0
        ):
            raise ValueError("revision_collection_or_execution_failed")
        expected = plan({"source_sha": expected_revision, "nodes": suite})["groups"][
            record["shard"]
        ]["node_ids"]
        execution = record["execution"]
        if (
            record["expected"] != expected
            or execution is None
            or execution["exitstatus"] != 0
            or Counter(execution["nodes"]) != Counter(expected)
        ):
            raise ValueError("incomplete_selected_tests")
        events = execution["reports"]
        if any(e["nodeid"] not in expected or e["xfail"] for e in events):
            raise ValueError("unexpected_or_xfailed_case")
        for node in expected:
            outcomes = [(e["when"], e["outcome"]) for e in events if e["nodeid"] == node]
            if Counter(outcomes) != Counter(
                {("setup", "passed"): 1, ("call", "passed"): 1, ("teardown", "passed"): 1}
            ):
                raise ValueError("failed_skipped_missing_or_duplicate_test_phase")
        all_expected.extend(expected)
        summary.append(
            {
                "shard": record["shard"],
                "tests": len(expected),
                "elapsed_seconds": record["elapsed_seconds"],
            }
        )
    if Counter(all_expected) != Counter(suite):
        raise ValueError("coverage_omission_or_overlap")
    return {
        "source_sha": expected_revision,
        "test_baseline_sha": BASE,
        "total_passed": len(suite),
        "missing": 0,
        "duplicates": 0,
        "shards": sorted(summary, key=lambda r: r["shard"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    execute_parser = sub.add_parser("run")
    execute_parser.add_argument("--shard", choices=tuple(GROUPS), required=True)
    aggregate = sub.add_parser("verify")
    aggregate.add_argument("--artifacts", type=Path, required=True)
    aggregate.add_argument("--revision", required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "run":
        raise SystemExit(execute(args.shard))
    result = verify(args.artifacts, args.revision)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
