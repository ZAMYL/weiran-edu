"""Independent exact scoring over frozen synthetic inputs; never sent to Planner."""

import json
import re
from fractions import Fraction as F
from math import sqrt
from operator import eq, ge, gt, le, lt
from pathlib import Path

from evals.research_v21_review import generate_review_cases as reference

ROOT = Path(__file__).resolve().parents[2] / "evals/research_v21_review"
CASE_ID = "review_decimal_interaction_20261103"
OPS = {"==": eq, "<": lt, "<=": le, ">": gt, ">=": ge}


def group_signature(target, remainder, items, outcome):
    return "group:" + repr((sorted((tuple(target), tuple(remainder))), sorted(items), outcome))


def grade(result, executor):
    case = next(
        c for c in json.loads((ROOT / "inputs.json").read_bytes()) if c["case_id"] == CASE_ID
    )
    oracle = next(
        c for c in json.loads((ROOT / "oracles.json").read_bytes()) if c["case_id"] == CASE_ID
    )
    rows = case["rows"]
    ids = [i["item_id"] for i in case["items"]]
    all_rows = list(range(len(rows)))
    targets = {"stats:" + i for i in ids}
    targets.update(
        "relation:" + ":".join(sorted((r["left"], r["right"]))) for r in oracle["relations"]
    )
    for group in oracle["groups"]:
        conditions = group["conditions_all_equal"]
        a = [
            i
            for i, row in enumerate(rows)
            if all(F(row[ids.index(k)]) == F(v) for k, v in conditions.items())
        ]
        b = [i for i in all_rows if i not in a]
        targets.add(group_signature(a, b, conditions, group["outcome"]))
    observed, qualified, wrong, source_errors = set(), [], [], []
    lineage_only = 0
    source_checks = 0
    for fact in executor.facts.values():
        executor._verify(fact)
        original = (
            executor.facts[fact.query["arguments"]["fact_id"]]
            if fact.tool == "inspect_discovery_support"
            else fact
        )
        name, args = original.tool, original.query["arguments"]
        expected_sources, semantic = {}, []
        if name == "get_dimension_statistics":
            for item in original.view["items"]:
                if item.get("state") != "available":
                    continue
                expected = reference.stats(case, item["item_id"])
                mean = item["mean"]
                actual = None if mean is None else F(mean["numerator"], mean["denominator"])
                if (item["valid_count"], item["denominator"], actual) != (
                    expected["valid_count"],
                    expected["denominator"],
                    F(expected["mean"]),
                ):
                    wrong.append(fact.fact_id)
                semantic.append("stats:" + item["item_id"])
            expected_sources = {s.key: all_rows for s in fact.item_sources if ".bin" not in s.key}
        elif name == "query_dimension_relation":
            paired, (xx, yy, xy) = reference.independent_rank_moments(
                case, args["left"], args["right"]
            )
            expected = float(xy) / sqrt(float(xx * yy)) if xx and yy else None
            v = original.calculation.values
            if (
                v["paired_count"] != len(paired)
                or v["excluded_count"] != len(rows) - len(paired)
                or (v["rho"] is None) != (expected is None)
                or expected is not None
                and abs(v["rho"] - expected) > 1e-12
            ):
                wrong.append(fact.fact_id)
            expected_sources = {
                "left.paired": [i - 1 for i in paired],
                "right.paired": [i - 1 for i in paired],
            }
            semantic.append("relation:" + ":".join(sorted((args["left"], args["right"]))))
        elif name == "compare_student_groups":
            conditions = args["conditions"]
            a = [
                i
                for i, row in enumerate(rows)
                if all(
                    OPS[p["operator"]](F(row[ids.index(p["item_id"])]), F(p["threshold"]))
                    for p in conditions
                )
            ]
            b = [i for i in all_rows if i not in a]
            outcome = args["outcomes"][0]
            j = ids.index(outcome)
            ma = sum((F(rows[i][j]) for i in a), F()) / len(a) if a else None
            mb = sum((F(rows[i][j]) for i in b), F()) / len(b) if b else None
            v = original.calculation.values
            if (
                v["target_count"],
                v["remainder_count"],
                v["target_valid"],
                v["remainder_valid"],
                v["target_mean"],
                v["remainder_mean"],
                v["difference"],
                v["denominator"],
                v["eligible_denominator"],
            ) != (
                len(a),
                len(b),
                len(a),
                len(b),
                ma,
                mb,
                ma - mb if ma is not None and mb is not None else None,
                len(rows),
                len(rows),
            ):
                wrong.append(fact.fact_id)
            expected_sources = {"target.valid": a, "remainder.valid": b, "unevaluable.rows": []}
            for side, members in (("target", a), ("remainder", b)):
                for p in conditions:
                    expected_sources[side + ".condition." + p["item_id"]] = members
            semantic.append(group_signature(a, b, [p["item_id"] for p in conditions], outcome))
        elif name == "inspect_item_quality":
            if original.calculation.values["counts"] != {"valid": len(rows)}:
                wrong.append(fact.fact_id)
            expected_sources = {"state.valid": all_rows}
            semantic = []  # Counts already present in the trusted summary are not new facts.
        else:
            wrong.append(fact.fact_id)
        for source in fact.item_sources:
            source_checks += 1
            indexes = [int(pid[1:]) for pid in source.participants]
            if source.key in expected_sources:
                if indexes != expected_sources[source.key]:
                    source_errors.append(fact.fact_id)
            else:
                match = re.fullmatch(r"[IQ][0-9]{2,3}\.bin([0-3])", source.key)
                if match and name == "get_dimension_statistics":
                    index = int(match[1])
                    low, high = F(case["scale"]["min"]), F(case["scale"]["max"])
                    lower = low + (high - low) * index / 4
                    upper = low + (high - low) * (index + 1) / 4
                    column = ids.index(source.item_id)
                    expected = [
                        i
                        for i, row in enumerate(rows)
                        if lower <= F(row[column])
                        and (F(row[column]) <= upper if index == 3 else F(row[column]) < upper)
                    ]
                    if indexes != expected:
                        source_errors.append(fact.fact_id)
                else:
                    lineage_only += 1
                    source_errors.append(fact.fact_id)
            for pid, ref in zip(source.participants, source.references, strict=True):
                witness = executor.kernel.witnesses[ref]
                i, j = int(pid[1:]), ids.index(source.item_id)
                if (
                    witness.participant_id != pid
                    or witness.item_id != source.item_id
                    or witness.source_sha256 != executor.source.fingerprint
                    or witness.kind == "score"
                    and F(witness.value) != F(rows[i][j])
                ):
                    source_errors.append(fact.fact_id)
        if fact.fact_id not in wrong and fact.fact_id not in source_errors:
            observed.update(semantic)
            if original.view["qualified"] and fact.tool != "inspect_discovery_support":
                qualified.extend(semantic)
    seeds = set(result["retained_fact_ids"])
    return {
        "matched_targets": len(targets & observed),
        "target_denominator": len(targets),
        "uncovered_targets": sorted(targets - observed),
        "checked_semantics": sorted(observed),
        "qualified_semantics": sorted(set(qualified)),
        "wrong_facts": len(set(wrong)),
        "source_errors": len(set(source_errors)),
        "source_checks": source_checks,
        "source_lineage_only_checks": lineage_only,
        "independently_ungraded_extensions": ["coarse_bin_boundaries"] if lineage_only else [],
        "duplicate_facts": len(qualified) - len(set(qualified)),
        "repeat_tool_attempts": sum(
            t["error"] in ("repeated_tool", "duplicate_discovery") for t in result["tool_ledger"]
        ),
        "retained_seed_fraction": [len(seeds & set(executor.facts)), len(seeds)],
        "model_requests": result["model_requests"],
        "tool_attempts": result["tool_attempts"],
        "stop_reason": result["stop_reason"],
        "stop_claim_matches_coverage": not (
            result["investigation_coverage"]["uncovered_direction_ids"]
            and result["stop_reason"] == "goal_satisfied"
        ),
        "independent_semantic_stop_accuracy": None,
        "stop_scoring_limitation": "oracle specifies facts, not pedagogical stopping adequacy",
        "early_stop_with_frontier_and_budget": (
            result["stop_reason"] == "stopped_with_uncovered_directions"
            and result["investigation_coverage"].get("can_continue", False)
        ),
        "illegal_or_invalid_model_stop": result["stop_reason"]
        in {
            "invalid_model_response",
            "invalid_tool_arguments",
            "unknown_fact_id",
            "unavailable_tool",
        },
        "rejected_tool_attempts": sum(t["status"] == "rejected" for t in result["tool_ledger"]),
        "elapsed_seconds": result["elapsed_seconds"],
        "verification_replays_including_scoring": executor.replay_executions,
    }
