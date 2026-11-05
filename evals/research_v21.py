"""V2.1 original regression plus reviewer-supplied cases. Answers stay in the scorer."""

import argparse
import asyncio
import json
from fractions import Fraction
from hashlib import sha256
from math import sqrt
from pathlib import Path
from tempfile import TemporaryDirectory

from evals import research_agent_v2 as original
from evals.research_v21_review import generate_review_cases as reference
from prototypes.research_agent_v2.demo import offline
from prototypes.research_agent_v2.review_sources import RECIPES

REVIEW = Path(__file__).resolve().parent / "research_v21_review"


def rational(value):
    return None if value is None else Fraction(value["numerator"], value["denominator"])


def group_key(conditions, outcome):
    return (
        "group:"
        + repr(sorted((i, str(Fraction(v))) for i, v in conditions.items()))
        + "->"
        + outcome
    )


def review_grade(result, executor, case, oracle):
    errors, source_errors, ungraded = [], [], []
    observed, qualified = set(), set()
    checks = 0
    item_ids = [i["item_id"] for i in case["items"]]
    targets = {"stats:" + item for item in oracle["item_statistics"]}
    targets.update(
        "relation:" + ":".join(sorted((r["left"], r["right"]))) for r in oracle["relations"]
    )
    targets.update(group_key(g["conditions_all_equal"], g["outcome"]) for g in oracle["groups"])
    for fact in executor.facts.values():
        executor._verify(fact)
        origin = (
            executor.facts[fact.query["arguments"]["fact_id"]]
            if fact.tool == "inspect_discovery_support"
            else fact
        )
        query = origin.query
        args, name = query["arguments"], query["tool"]
        expected_sources = {}
        semantics = []
        if name == "get_dimension_statistics":
            for item in fact.view.get("items", []):
                if item.get("state") != "available":
                    continue  # privacy-withheld is unexecuted, never auto-correct.
                item_id = item["item_id"]
                expected = oracle["item_statistics"][item_id]
                semantics.append("stats:" + item_id)
                if (
                    item["denominator"] != expected["denominator"]
                    or item["valid_count"] != expected["valid_count"]
                    or rational(item["mean"]) != Fraction(expected["mean"])
                ):
                    errors.append(fact.fact_id)
            for source in fact.item_sources:
                stats = oracle["item_statistics"][source.item_id]
                expected_sources[source.key] = (
                    stats["missing_declaration_source"]["row_indices_1_based"]
                    if source.key.endswith(".missing")
                    else stats["valid_source"]["row_indices_1_based"]
                )
        elif name == "query_dimension_relation":
            ids, (xx, yy, xy) = reference.independent_rank_moments(
                case, args["left"], args["right"]
            )
            rho = float(xy) / sqrt(float(xx * yy)) if xx and yy else None
            v = origin.calculation.values
            if (
                v["paired_count"] != len(ids)
                or v["excluded_count"] != len(case["rows"]) - len(ids)
                or (v["rho"] is None) != (rho is None)
                or rho is not None
                and abs(v["rho"] - rho) > 1e-12
            ):
                errors.append(fact.fact_id)
            expected_sources = {"left.paired": ids, "right.paired": ids}
            semantics.append("relation:" + ":".join(sorted((args["left"], args["right"]))))
        elif name == "compare_student_groups":
            conditions = {}
            for p in args["conditions"]:
                column = item_ids.index(p["item_id"])
                minimum = min(
                    Fraction(row[column]) for row in case["rows"] if row[column] is not None
                )
                # Equality to the observed minimum is equivalent to <= that minimum.
                if p["operator"] not in ("==", "<=") or Fraction(p["threshold"]) != minimum:
                    ungraded.append(fact.fact_id)
                    break
                conditions[p["item_id"]] = p["threshold"]
            else:
                expected = reference.group(case, conditions, args["outcomes"][0])
                v = origin.calculation.values
                a, b = expected["target"], expected["remainder"]
                if (
                    v["target_count"],
                    v["remainder_count"],
                    v["target_valid"],
                    v["remainder_valid"],
                    v["target_mean"],
                    v["remainder_mean"],
                    v["difference"],
                ) != (
                    a["count"],
                    b["count"],
                    a["outcome_valid_count"],
                    b["outcome_valid_count"],
                    Fraction(a["mean"]) if a["mean"] else None,
                    Fraction(b["mean"]) if b["mean"] else None,
                    Fraction(expected["difference"]) if expected["difference"] else None,
                ):
                    errors.append(fact.fact_id)
                expected_sources = {
                    "target.valid": a["outcome_source"]["row_indices_1_based"],
                    "remainder.valid": b["outcome_source"]["row_indices_1_based"],
                }
                for side, value in (("target", a), ("remainder", b)):
                    expected_sources.update(
                        {
                            side + ".condition." + s["item_id"]: s["row_indices_1_based"]
                            for s in value["condition_sources"]
                        }
                    )
                expected_sources["unevaluable.rows"] = [
                    i
                    for i, row in enumerate(case["rows"], 1)
                    if any(row[item_ids.index(item)] is None for item in conditions)
                ]
                semantics.append(group_key(conditions, args["outcomes"][0]))
        elif name == "inspect_item_quality":
            item = args["item_id"]
            expected = oracle["item_statistics"][item]
            counts = {
                k: n
                for k, n in {
                    "valid": expected["valid_count"],
                    "missing": expected["missing_count"],
                }.items()
                if n
            }
            if origin.calculation.values["counts"] != counts:
                errors.append(fact.fact_id)
            expected_sources = {
                "state.valid": expected["valid_source"]["row_indices_1_based"],
                "state.missing": expected["missing_declaration_source"]["row_indices_1_based"],
            }
            # Repeating a summary count is not new knowledge.
            semantics.append("stats:" + item)
        for source in fact.item_sources:
            checks += 1
            expected_rows = expected_sources.get(source.key)
            if expected_rows is None:
                ungraded.append(fact.fact_id)
                continue
            actual_rows = [int(pid[1:]) + 1 for pid in source.participants]
            if actual_rows != expected_rows or len(source.references) != len(expected_rows):
                source_errors.append(fact.fact_id)
            for pid, ref in zip(source.participants, source.references, strict=True):
                w = executor.kernel.witnesses[ref]
                row = int(pid[1:])
                col = item_ids.index(source.item_id)
                if w.participant_id != pid or w.item_id != source.item_id:
                    source_errors.append(fact.fact_id)
                if w.kind == "score" and (
                    w.value is None
                    or case["rows"][row][col] is None
                    or Fraction(w.value) != Fraction(case["rows"][row][col])
                ):
                    source_errors.append(fact.fact_id)
        if fact.fact_id not in errors and fact.fact_id not in ungraded:
            observed.update(semantics)
            if fact.view["qualified"]:
                qualified.update(semantics)
    return {
        "wrong_facts": len(set(errors)),
        "source_errors": len(set(source_errors)),
        "ungraded_facts": len(set(ungraded)),
        "source_checks": checks,
        "checked_semantics": sorted(observed),
        "qualified_semantics": sorted(qualified),
        "oracle_target_coverage": {"matched": len(targets & observed), "denominator": len(targets)},
        "uncovered_oracle_targets": sorted(targets - observed),
        "retained_seed_fraction": [
            len(set(result["retained_fact_ids"]) & set(executor.facts)),
            len(result["retained_fact_ids"]),
        ],
        "model_requests": result["model_requests"],
        "tool_attempts": result["tool_attempts"],
        "repeated_attempts": sum(
            t["error"] in ("repeated_tool", "duplicate_discovery") for t in result["tool_ledger"]
        ),
        "stop": result["stop_reason"],
        "coverage": result["investigation_coverage"],
        "verification_replay_executions": result["verification_replay_executions"],
        "elapsed_seconds": result["elapsed_seconds"],
        "actual_tokens": None,
        "actual_cost_cny": None,
    }


async def evaluate():
    regression = await original.evaluate()
    # This file was authored independently; neither case names nor answers go to the planner.
    inputs = json.loads((REVIEW / "inputs.json").read_bytes())
    answers = {a["case_id"]: a for a in json.loads((REVIEW / "oracles.json").read_bytes())}
    out = []
    with TemporaryDirectory(prefix="research-v21-review-") as directory:
        for recipe, case_id in RECIPES.items():
            case = next(c for c in inputs if c["case_id"] == case_id)
            row = {"review_case": case_id, "runs": {}, "grades": {}}
            for profile, fixed in (("fixed", True), ("v21_http_mock", False)):
                result, executor = await offline(
                    recipe, "识别值得优先核查的评分异常", Path(directory) / profile, fixed=fixed
                )
                row["runs"][profile] = result
                row["grades"][profile] = review_grade(result, executor, case, answers[case_id])
            a, b = row["grades"]["fixed"], row["grades"]["v21_http_mock"]
            row["added_correct_semantics"] = len(
                set(b["checked_semantics"]) - set(a["checked_semantics"])
            )
            row["missed_baseline_semantics"] = len(
                set(a["checked_semantics"]) - set(b["checked_semantics"])
            )
            out.append(row)
    return {
        "version": "research-v21-0.1",
        "original_three": regression,
        "review_cases": out,
        "review_case_denominator": len(inputs),
        "reviewer": "isolated_context_agent_not_human_approval",
        "reviewer_limitation": "saw statistical Kernel, not strategy, existing cases or answers",
        "review_input_sha256_lf": sha256(
            (REVIEW / "inputs.json").read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest(),
        "review_oracle_sha256_lf": sha256(
            (REVIEW / "oracles.json").read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest(),
        "real_qwen": "not_run",
        "paid_requests": 0,
        "model_quality_proven": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("new_output_required")
    result = asyncio.run(evaluate())
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    for row in result["review_cases"]:
        print(json.dumps({k: v for k, v in row.items() if k != "runs"}, ensure_ascii=False))
    return int(
        any(
            g["wrong_facts"] or g["source_errors"] or g["ungraded_facts"]
            for row in result["review_cases"]
            for g in row["grades"].values()
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
