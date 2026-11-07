"""Run exact #78 read-only and this revision on identical closed synthetic inputs."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .synthetic import RECIPES

BASE_SHA = "578b0b20c80510ade792c0fb02af2e6b1968a3dd"
FACT_FIELDS = (
    "profile_id",
    "batch_id",
    "snapshot_id",
    "calculation_rule_version",
    "metrics",
    "dimensions",
    "conditions",
    "exploration_query",
    "source_queries",
    "agent_fact_scope",
)


def _revision(root: Path) -> str:
    subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=root, check=True)
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip():
        raise ValueError("clean_source_worktree_required")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _run(root: Path, output: Path) -> None:
    # Only OS startup variables; do not propagate credentials to the child experiment.
    env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "TEMP", "TMP") if k in os.environ}
    env.update(PYTHONUTF8="1", PYTHONPATH=os.pathsep.join((str(root), str(root / "apps/api"))))
    subprocess.run(
        [sys.executable, "-m", "prototypes.capability_profiles.demo", "--output-dir", str(output)],
        cwd=root,
        env=env,
        check=True,
    )


def compare(before: Path, after: Path) -> list[dict]:
    rows = []
    for name in ("five", *RECIPES):
        a = json.loads((before / (name + ".technical.json")).read_text(encoding="utf-8"))
        b = json.loads((after / (name + ".technical.json")).read_text(encoding="utf-8"))
        ai = {p["profile_id"]: p for p in a["profiles"]}
        bi = {p["profile_id"]: p for p in b["profiles"]}
        if list(ai) != list(bi):
            raise ValueError("profile_selection_changed")
        for key in ai:
            if any(ai[key][f] != bi[key][f] for f in FACT_FIELDS):
                raise ValueError("profile_facts_changed")

        def plan(r):
            return [(t["tool"], t.get("arguments"), t["status"]) for t in r["tool_ledger"]]

        if plan(a) != plan(b) or any(
            a[k] != b[k] for k in ("status", "stop_reason", "model_requests", "tool_attempts")
        ):
            raise ValueError("investigation_behavior_changed")

        def observations(r):
            return [
                {
                    "batch_id": f["batch_id"],
                    "snapshot_id": f["snapshot_id"],
                    "query": f["query"],
                    "rule_version": f["rule_version"],
                    "values": {k: v for k, v in f["values"].items() if k != "fact_id"},
                }
                for f in r["verified_observations"]
            ]

        if observations(a) != observations(b):
            raise ValueError("verified_tool_observations_changed")
        if a["real_qwen"] != "not_run" or b["real_qwen"] != "not_run":
            raise ValueError("unexpected_live_execution")
        rows.append(
            {
                "synthetic_case": name,
                "profiles": len(ai),
                "facts_unchanged": True,
                "same_tool_queries_and_budget_counts": True,
                "model_requests": b["model_requests"],
                "tool_attempts": b["tool_attempts"],
                "before_titles": [p["title"] for p in ai.values()],
                "after_titles": [p["title"] for p in bi.values()],
                "before_summary": [p["summary"] for p in ai.values()],
                "after_summary": [p["summary"] for p in bi.values()],
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-worktree", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    baseline = args.baseline_worktree.resolve()
    root = Path(__file__).resolve().parents[2]
    output = args.output_dir.resolve()
    if any(r == output or r in output.parents for r in (baseline, root)):
        parser.error("external_new_output_directory_required")
    if _revision(baseline) != BASE_SHA:
        parser.error("exact_read_only_baseline_required")
    current = _revision(root)
    output.mkdir(parents=True, exist_ok=False)
    _run(baseline, output / "before")
    _run(root, output / "after")
    if _revision(baseline) != BASE_SHA or _revision(root) != current:
        raise ValueError("source_revision_changed")
    rows = compare(output / "before", output / "after")
    result = {
        "before_sha": BASE_SHA,
        "after_sha": current,
        "cases": rows,
        "text_origin": "rule_generated_from_verified_statistics",
        "real_qwen": "not_run",
    }
    (output / "readability-comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = ["# 教师画像文字改进对照", "", "只改变规则解释；HTTP Mock选择、原始统计和来源不变。", ""]
    for row in rows:
        md += [
            "## " + row["synthetic_case"],
            "",
            f"画像{row['profiles']}张；事实、查询及预算计数均相同。",
            "",
        ]
        for title_a, title_b, summary_a, summary_b in zip(
            row["before_titles"],
            row["after_titles"],
            row["before_summary"],
            row["after_summary"],
            strict=True,
        ):
            md += ["修改前：" + title_a, "", summary_a, "", "修改后：" + title_b, "", summary_b, ""]
    (output / "changes.md").write_text("\n".join(md), encoding="utf-8")
    print(
        json.dumps(
            {
                "before_sha": BASE_SHA,
                "after_sha": current,
                "cases_verified": len(rows),
                "real_qwen": "not_run",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
