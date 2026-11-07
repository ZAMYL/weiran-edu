"""Offline-only reproducible comparisons; no production API, credentials or remote model."""

import argparse
import asyncio
import json
import subprocess
from collections import Counter
from pathlib import Path

from app.analysis.edu.discovery import discover
from app.analysis.edu.discovery.models import GroupComparison

from prototypes.adaptive_assessment.live.safety import LIVE_DISABLED
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.research_agent_v2.demo import offline

from .agent import run_profiles
from .engine import DIMS, dataset_from_source
from .synthetic import RECIPES


def _display(value):
    return "未提供" if value is None else f"{value['numerator'] / value['denominator']:.2f}"


def teacher_markdown(result: dict) -> str:
    lines = ["# 本次测评的典型评分画像", "", "合成演示；选择由HTTP Mock完成，真实Qwen未运行。", ""]
    if result["status"] != "completed":
        reasons = {
            "model_budget_exhausted": "模型请求额度已用尽",
            "tool_budget_exhausted": "工具调用额度已用尽",
            "model_timeout": "规划请求超时",
            "run_timeout": "本轮执行超时",
        }
        lines += [
            "本轮调查未完成，不能据此判断有没有典型群体。",
            reasons.get(result["stop_reason"], "本轮未成功完成，请核对技术记录。"),
        ]
    elif not result["profiles"]:
        lines += [
            "本轮搜索范围内暂无符合展示条件的群体。"
            if result["profile_stop"] == "no_eligible_profiles"
            else "本轮尚未选择已核验画像，仍可核查候选。",
            "这不代表所有人的表现完全相同，也不代表已经穷尽所有组合。",
        ]
    for profile in result["profiles"]:
        lines += [
            "## " + profile["title"],
            "",
            profile["common_pattern"],
            "",
            profile["summary"],
            "",
            "分析范围：" + str(profile["metrics"]["student_denominator"]) + "人。",
            "",
            "|评价维度|本组均值|其他可判定参评者|差值（本组减其他）|",
            "|---|---:|---:|---:|",
        ]
        for d in profile["dimensions"]:
            name = next(dim.value for dim in DIMS if dim.name == d["dimension"])
            if d["condition_defined"]:
                name += "（筛选条件）"
            lines.append(
                f"|{name}|{_display(d['target']['mean'])}|"
                f"{_display(d['remainder']['mean'])}|{_display(d['difference'])}|"
            )
        lines += [
            "",
            profile["why_show"],
            "",
            "下一步：" + profile["suggested_next_action"],
            "",
            "限制：" + "；".join(profile["limitations"]),
            "",
        ]
    lines += [
        "",
        "群体可以重叠，人数不能相加；评分与原始评价依据需在正式授权接口中核对。",
        "本原型未读取任何教师文件或文字评语，不提供因果或综合能力等级。",
    ]
    return "\n".join(lines) + "\n"


def _semantic_key(discovery):
    p = discovery.payload
    if not isinstance(p, GroupComparison):
        return None
    return (
        frozenset((frozenset(p.selection.matches), frozenset(p.remainder))),
        p.outcome_dimension,
    )


async def demonstrate(output: Path) -> dict:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True
    ).strip()
    output.mkdir(parents=True, exist_ok=False)
    comparison = []
    for name in ("five", *RECIPES):
        source = admit(name)
        data, kernel = dataset_from_source(source)
        baseline = discover(
            data, batch_id=kernel.data.batch.batch_id, snapshot_id=source.fingerprint
        )
        result, book, _ = await run_profiles(name, output / "state" / name)
        old, _ = await offline(name, "优先核查", output / "old-research-state" / name)
        (output / (name + ".baseline-research.json")).write_text(
            json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output / (name + ".teacher.md")).write_text(teacher_markdown(result), encoding="utf-8")
        (output / (name + ".technical.json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Differences from default20 are search policy, never claimed as Qwen invention.
        baseline_keys = {_semantic_key(d) for d in baseline.findings}
        selected_keys = {
            _semantic_key(c.discovery)
            for c in book.candidates
            if any(p["profile_id"] == c.profile_id for p in result["profiles"])
        }
        comparison.append(
            {
                "synthetic_case": name,
                "students": len(kernel.participants),
                "fixed_default": {
                    "types": dict(Counter(d.type for d in baseline.findings)),
                    "search_attempts": len(baseline.search_audit),
                    "group_cards": sum(d.type == "group_difference" for d in baseline.findings),
                    "distinct_member_partitions": len(
                        {k[0] for k in baseline_keys if k is not None}
                    ),
                },
                "original_research_http_mock": {
                    "status": old["status"],
                    "finding_types": [c["type"] for c in old["findings"]],
                    "model_requests": old["model_requests"],
                    "tool_attempts": old["tool_attempts"],
                    "stop_reason": old["stop_reason"],
                    "elapsed_session_seconds": old["elapsed_seconds"],
                },
                "profile_http_mock": {
                    "status": result["status"],
                    "profiles": len(result["profiles"]),
                    "model_requests": result["model_requests"],
                    "tool_attempts": result["tool_attempts"],
                    "uninvestigated_candidates": result["uninvestigated_candidates"],
                    "search": result["profile_search"],
                    "extra_selected_semantics_vs_default20": len(selected_keys - baseline_keys),
                    "extra_coverage_is_bounded_search_not_model_increment": True,
                    "new_statistical_formula": False,
                    "source_verified": all(p["source_replay_verified"] for p in result["profiles"]),
                    "elapsed_session_seconds": result["elapsed_seconds"],
                    "candidate_search_seconds": result["candidate_search_seconds"],
                    "elapsed_total_seconds": result["elapsed_total_seconds"],
                },
                "real_qwen": "not_run",
                "actual_tokens": None,
                "actual_cost_cny": None,
            }
        )
        print(json.dumps(comparison[-1], ensure_ascii=False), flush=True)
    report = {
        "source_sha": revision,
        "comparison": comparison,
        "production_enabled": False,
        "real_qwen": "not_run",
        "oracle_changed": False,
        "conclusion": ("新增整群五维画像、重复压缩与可回查条件；不证明新统计能力或真实模型优势。"),
    }
    (output / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        parser.error(LIVE_DISABLED)
    asyncio.run(demonstrate(args.output_dir))


if __name__ == "__main__":
    main()
