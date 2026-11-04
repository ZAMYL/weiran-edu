"""Independent held-out scoring. No oracle or fixture label is exposed to the planner."""

import argparse
import asyncio
import json
from fractions import Fraction as F
from operator import eq, ge, gt, le, lt
from pathlib import Path
from statistics import correlation
from tempfile import TemporaryDirectory

from app.ai.edu.discovery.models import AgentError, Limits

from prototypes.research_agent_v2.demo import offline

# Written/frozen before evaluating the held-out runner. No SUT/recipe builder import.
CASES = (
    ("research-v2-final-interaction", 64, 8, "competency", 9001),
    ("research-v2-final-relation", 80, 11, "question", 9007),
    ("research-v2-final-empty", 36, 4, "constant", 9011),
)


def rows_for(n, m, kind, seed):
    rows = []
    for i in range(n):
        a, b = (i + seed) % 2, (i // 2 + seed // 2) % 2
        if kind == "constant":
            row = [F(3)] * m
        elif kind == "competency":
            row = [F(1 + 4 * a), F(1 + 4 * b), F(1 if a == b else 5)] + [F(3)] * (m - 3)
        else:
            row = [F(20 + 60 * a), F(80 - 60 * a)]
            row += [F(20 + 60 * ((i >> (j % 3)) % 2)) for j in range(2, m)]
        rows.append(row)
    return rows


def rank(values):
    return [F(sum(x < v for x in values)) + F(1 + sum(x == v for x in values), 2) for v in values]


def grade(result, executor, rows) -> dict:
    errors, source_errors, signatures = [], [], []
    source_count = 0
    operations = {"<": lt, "<=": le, "==": eq, ">=": ge, ">": gt}

    def target_sets(query):
        args = query["arguments"]
        valid = list(range(len(rows)))
        if query["tool"] == "compare_student_groups":
            target = [
                i
                for i in valid
                if all(
                    operations[p["operator"]](rows[i][int(p["item_id"][1:]) - 1], F(p["threshold"]))
                    for p in args["conditions"]
                )
            ]
            return target, [i for i in valid if i not in target]
        return valid, valid

    for fact in executor.facts.values():
        executor._verify(fact)
        origin = (
            executor.facts[fact.query["arguments"]["fact_id"]]
            if fact.tool == "inspect_discovery_support"
            else fact
        )
        targets, remainder = target_sets(origin.query)
        for source in fact.item_sources:
            source_count += 1
            indexes = (
                targets
                if source.key.startswith("target.")
                else remainder
                if source.key.startswith("remainder.")
                else []
                if source.key == "unevaluable.rows"
                else list(range(len(rows)))
            )
            expected_pids = tuple(f"S{i:04}" for i in indexes)
            if source.participants != expected_pids or len(source.references) != len(indexes):
                source_errors.append(fact.fact_id)
            for pid, ref in zip(source.participants, source.references, strict=True):
                witness = executor.kernel.witnesses[ref]
                i, j = int(pid[1:]), int(source.item_id[1:]) - 1
                if witness.participant_id != pid or witness.item_id != source.item_id:
                    source_errors.append(fact.fact_id)
                if witness.kind == "score" and (
                    witness.value is None or F(witness.value) != rows[i][j]
                ):
                    source_errors.append(fact.fact_id)
        if fact.calculation is None:
            for item in fact.view.get("items", []):
                j = int(item["item_id"][1:]) - 1
                values = [r[j] for r in rows]
                expected_mean = sum(values, F()) / len(values)
                actual = item.get("mean")
                actual_mean = F(actual["numerator"], actual["denominator"]) if actual else None
                if item.get("valid_count") != len(rows) or actual_mean != expected_mean:
                    errors.append(fact.fact_id)
            continue
        v = fact.calculation.values
        if fact.calculation.kind == "item_relation":
            left, right = int(v["left"][1:]) - 1, int(v["right"][1:]) - 1
            x, y = [r[left] for r in rows], [r[right] for r in rows]
            expected = (
                correlation(rank(x), rank(y)) if len(set(x)) > 1 and len(set(y)) > 1 else None
            )
            if v["rho"] != expected or v["paired_count"] != len(rows):
                errors.append(fact.fact_id)
        elif fact.calculation.kind == "item_group_difference":
            j = int(v["outcome"][1:]) - 1
            a = sum((rows[i][j] for i in targets), F()) / len(targets) if targets else None
            b = sum((rows[i][j] for i in remainder), F()) / len(remainder) if remainder else None
            if (
                v["target_count"],
                v["remainder_count"],
                v["target_mean"],
                v["remainder_mean"],
                v["difference"],
                v["denominator"],
                v["eligible_denominator"],
            ) != (
                len(targets),
                len(remainder),
                a,
                b,
                a - b if a is not None and b is not None else None,
                len(rows),
                len(rows),
            ):
                errors.append(fact.fact_id)
        if fact.view["qualified"]:
            signatures.append(
                json.dumps(
                    {"type": fact.calculation.kind, "values": str(fact.calculation.values)},
                    sort_keys=True,
                )
            )
    total = len(executor.facts)
    return {
        "wrong_facts": len(set(errors)),
        "source_errors": len(set(source_errors)),
        "qualified_semantics": signatures,
        "duplicates": len(signatures) - len(set(signatures)),
        "verified_fact_count": total,
        "source_count": source_count,
        "fact_error_fraction": [len(set(errors)), total] if total else None,
        "source_correct_fraction": [total - len(set(source_errors)), total] if total else None,
        "retained_seed_fraction": [
            len(set(result["retained_fact_ids"]) & set(executor.facts)),
            len(result["retained_fact_ids"]),
        ],
        "repeat_attempts": sum(
            t["error"] in ("repeated_tool", "duplicate_discovery") for t in result["tool_ledger"]
        ),
        "model_requests": result["model_requests"],
        "tool_attempts": result["tool_attempts"],
        "verification_replay_executions": result["verification_replay_executions"],
        "elapsed_seconds": result["elapsed_seconds"],
        "mock_token_usage": result["budget"]["charged_tokens"],
        "actual_tokens": result["actual_tokens"],
        "actual_cost_cny": None,
    }


async def evaluate() -> dict:
    output = []
    with TemporaryDirectory(prefix="research-v2-evaluation-") as d:
        for recipe, n, m, kind, seed in CASES:
            rows = rows_for(n, m, kind, seed)
            observations = {}
            runs = {}
            for name, fixed in (("fixed", True), ("agent_http_mock", False)):
                result, executor = await offline(
                    recipe, "识别值得优先核查的评分异常", Path(d) / name, fixed=fixed
                )
                runs[name] = result
                observations[name] = grade(result, executor, rows)
            a, b = observations["fixed"], observations["agent_http_mock"]
            # Scope-equivalent semantic comparison, never a count of newly minted Fact IDs.
            added = set(b["qualified_semantics"]) - set(a["qualified_semantics"])
            missed = set(a["qualified_semantics"]) - set(b["qualified_semantics"])
            independent_target = (
                "joint_difference"
                if kind == "competency"
                else "negative_relation"
                if kind == "question"
                else "zero_findings"
            )
            agent_target = (
                any(
                    f["type"] == "item_group_difference"
                    and abs(f["metrics"]["difference"]["numerator"]) == 8
                    and f["metrics"]["difference"]["denominator"] == 3
                    for f in runs["agent_http_mock"]["findings"]
                )
                if kind == "competency"
                else any(f["type"] == "item_relation" for f in runs["agent_http_mock"]["findings"])
                if kind == "question"
                else not runs["agent_http_mock"]["findings"]
            )
            output.append(
                {
                    "synthetic_case": recipe,
                    "seed": seed,
                    "target": independent_target,
                    "agent_target_met": agent_target,
                    "fixed": a,
                    "agent_http_mock": b,
                    "added_correct_semantics": len(added),
                    "missed_baseline_semantics": len(missed),
                    "runs": runs,
                    "qwen": {"status": "not_run", "actual_tokens": None, "actual_cost_cny": None},
                }
            )
        restricted = []
        for kind in ("access", "budget"):
            try:
                await offline(
                    CASES[0][0],
                    "识别值得优先核查的评分异常",
                    Path(d) / ("limited-" + kind),
                    access="denied" if kind == "access" else "synthetic_full",
                    limits=Limits(tool_attempts=1) if kind == "budget" else None,
                )
            except AgentError as error:
                restricted.append({"kind": kind, "status": "blocked", "reason": str(error)})
    return {
        "version": "research-v2-heldout-0.1",
        "origin": "offline_mechanism",
        "cases": output,
        "case_denominator": len(CASES),
        "restricted": restricted,
        "real_qwen": "not_run",
        "paid_requests": 0,
        "AI_incremental_value": "not_proven",
        "baseline": "existing AdaptiveFake bounded policy, not exhaustive discovery",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("new_output_required")
    result = asyncio.run(evaluate())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = ["# V2独立离线比较", "", "HTTP Mock机制，不代表真实Qwen效果。"]
    for row in result["cases"]:
        summary += [
            f"- {row['synthetic_case']}：新增{row['added_correct_semantics']}，"
            f"遗漏{row['missed_baseline_semantics']}；目标{row['agent_target_met']}。"
        ]
    summary += ["", "真实Qwen not_run；费用/Token未发生真实计量。"]
    args.output.with_suffix(".md").write_text("\n".join(summary), encoding="utf-8")
    print("\n".join(summary))
    return int(
        any(
            row[p]["wrong_facts"] or row[p]["source_errors"]
            for row in result["cases"]
            for p in ("fixed", "agent_http_mock")
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
