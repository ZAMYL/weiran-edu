"""Execute and report the DS-06A benchmark without network models or student code execution."""

from __future__ import annotations

import argparse
import csv
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from . import DEPENDENCIES, SYSTEMS
from .baseline import run_baseline
from .c01 import run_c01
from .metrics import score_result, semantic_signature, summarize
from .model import load_cases, load_oracle
from .pipelines import run_ds03

PIPELINES = {"A": run_baseline, "B": run_ds03, "C": run_c01}


def run_once(data_dir: Path, selected: str | None = None) -> list[dict]:
    rows = []
    for case in load_cases(data_dir, selected):
        for system in SYSTEMS:
            started = perf_counter()
            # Only the synthetic case enters a pipeline. The independently authored
            # oracle is loaded after the module has returned its actual output.
            result = PIPELINES[system](case)
            result["runtime_ms"] = (perf_counter() - started) * 1000
            oracle = load_oracle(data_dir, case)
            result["distribution"] = case.distribution
            result["metrics"] = score_result(case, oracle, result)
            rows.append(result)
    return rows


def run_benchmark(data_dir: Path, repeat: int = 1, selected: str | None = None) -> dict:
    repetitions = [run_once(data_dir, selected) for _ in range(repeat)]
    baseline = semantic_signature(repetitions[0])
    deterministic = all(semantic_signature(rows) == baseline for rows in repetitions[1:])
    results = repetitions[-1]
    timings = {
        f"{row['case_id']}:{row['system']}": [
            next(
                item["runtime_ms"]
                for item in rows
                if item["case_id"] == row["case_id"] and item["system"] == row["system"]
            )
            for rows in repetitions
        ]
        for row in results
    }
    return {
        "benchmark": "DS-06A",
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": DEPENDENCIES,
        "synthetic_data_only": True,
        "student_code_executed": False,
        "database_modified": False,
        "real_qwen_called": False,
        "model_reasoning_quality_measured": False,
        "real_token_usage": None,
        "real_model_cost": None,
        "offline_selector": "bounded actual-tool-output selector via httpx.MockTransport",
        "repeat_count": repeat,
        "semantic_repeat_deterministic": deterministic,
        "repeat_timings_ms": timings,
        "summary": summarize(results),
        "cases": results,
    }


def write_outputs(report: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_summary_csv(report, output_dir / "summary.csv")
    _write_case_csv(report, output_dir / "case_results.csv")
    (output_dir / "report.md").write_text(_markdown(report), encoding="utf-8")


def _write_summary_csv(report: dict, path: Path) -> None:
    fields = [
        "system",
        "cases",
        "completed",
        "rejected",
        "basic_statistics_accuracy",
        "phenomenon_discovery_accuracy",
        "evidence_localization_success_rate",
        "feature_extraction_accuracy",
        "association_accuracy",
        "unsupported_root_cause_assertions",
        "incomplete_evidence_correct_abstention_rate",
        "source_fact_consistency",
        "minimum_distribution_phenomenon_accuracy",
        "distribution_accuracy_range",
        "actual_runtime_ms",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for system in SYSTEMS:
            writer.writerow({"system": system, **report["summary"][system]})


def _write_case_csv(report: dict, path: Path) -> None:
    metric_names = list(report["cases"][0]["metrics"])
    fields = [
        "case_id",
        "distribution",
        "system",
        "status",
        "error",
        "runtime_ms",
        *metric_names,
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report["cases"]:
            writer.writerow(
                {
                    key: row[key]
                    for key in (
                        "case_id",
                        "distribution",
                        "system",
                        "status",
                        "error",
                        "runtime_ms",
                    )
                }
                | row["metrics"]
            )


def _markdown(report: dict) -> str:
    lines = [
        "# DS-06A 编程错误模式可复现评测结果",
        "",
        "> 本报告由 `python -m evaluation.ds06a.run_eval` 生成。数据全部为独立合成样本；",
        (
            "> 未执行提交代码、未修改数据库、未调用真实 Qwen。"
            "耗时是当前环境实测，Token 与真实费用为 N/A。"
        ),
        "",
        "## 总体结果",
        "",
        (
            "| 能力 | 基础统计 | 现象发现 | 证据定位 | 特征提取 | 关联/卡片 | "
            "无依据根因断言 | 正确弃答 | 来源一致 | 最低分布现象准确率 | 总耗时 ms |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for system in SYSTEMS:
        row = report["summary"][system]
        lines.append(
            f"| {system} | {_percent(row['basic_statistics_accuracy'])} | "
            f"{_percent(row['phenomenon_discovery_accuracy'])} | "
            f"{_percent(row['evidence_localization_success_rate'])} | "
            f"{_percent(row['feature_extraction_accuracy'])} | "
            f"{_percent(row['association_accuracy'])} | "
            f"{row['unsupported_root_cause_assertions']} | "
            f"{_percent(row['incomplete_evidence_correct_abstention_rate'])} | "
            f"{_percent(row['source_fact_consistency'])} | "
            f"{_percent(row['minimum_distribution_phenomenon_accuracy'])} | "
            f"{row['actual_runtime_ms']:.3f} |"
        )
    lines.extend(
        [
            "",
            f"重复运行语义结果一致：**{report['semantic_repeat_deterministic']}**"
            f"（repeat={report['repeat_count']}；不含耗时和随机 Fact ID）。",
            "",
            "## 分分布现象发现准确率",
            "",
            "| 分布 | A | B | C |",
            "|---|---:|---:|---:|",
        ]
    )
    distributions = sorted(
        {
            distribution
            for system in SYSTEMS
            for distribution in report["summary"][system]["distribution_phenomenon_accuracy"]
        }
    )
    for distribution in distributions:
        lines.append(
            f"| {distribution} | "
            + " | ".join(
                _percent(
                    report["summary"][system]["distribution_phenomenon_accuracy"].get(distribution)
                )
                for system in SYSTEMS
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 逐案状态与指标",
            "",
            "| 案例 | 系统 | 状态 | 现象 | 证据 | 弃答 | 来源 | 根因断言 | 耗时 ms |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["cases"]:
        metrics = row["metrics"]
        lines.append(
            f"| {row['case_id']} | {row['system']} | {row['status']}"
            f"{('/' + row['error']) if row['error'] else ''} | "
            f"{_percent(metrics['phenomenon_accuracy'])} | "
            f"{_ratio(metrics, 'evidence')} | {_ratio(metrics, 'abstention')} | "
            f"{_ratio(metrics, 'source')} | {metrics['unsupported_root_assertions']} | "
            f"{row['runtime_ms']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            (
                "- A 是本评测独立实现的确定性统计，不冒充 B-01 数据库接口；"
                "B/C 复用同一 A 作为组合基线。"
            ),
            "- B 的特征和关联来自真实 DS-03 `analyze` / `c01_feature_associations`。",
            (
                "- C 的多步卡片与证据来自真实 C-01 "
                "`investigate_programming` / `executor.resolve`；离线选择器只根据实际工具输出行动。"
            ),
            "- 统计准确率高不等于根因诊断正确；本基准专门检查关联不被提升为因果。",
            "- C 的隐私门限会抑制小单元；这类案例计入正确弃答，不计入其可发现现象分母。",
            "- 未验证真实 Qwen 推理质量、Token、费用、正式 Provider、B-03 持久化及网页端到端。",
            "",
        ]
    )
    return "\n".join(lines)


def _ratio(metrics: dict, prefix: str) -> str:
    if not metrics[prefix + "_total"]:
        return "N/A"
    return f"{metrics[prefix + '_success']}/{metrics[prefix + '_total']}"


def _percent(value) -> str:
    return "N/A" if value is None else f"{100 * value:.1f}%"


def main() -> None:
    package = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=package / "data")
    parser.add_argument("--output-dir", type=Path, default=package / "results")
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 11))
    parser.add_argument("--case", dest="selected")
    args = parser.parse_args()
    report = run_benchmark(args.data_dir, args.repeat, args.selected)
    write_outputs(report, args.output_dir)
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "semantic_repeat_deterministic": report["semantic_repeat_deterministic"],
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
