"""Independent scoring for DS-06A; expectations are read only after pipelines return."""

from __future__ import annotations

import json
from collections import defaultdict
from hashlib import sha256

from .model import Case


def score_result(case: Case, oracle: dict, result: dict) -> dict:
    system = result["system"]
    expected_rejection = oracle.get("rejections", {}).get(system)
    if expected_rejection is not None:
        passed = result["status"] == "rejected" and (
            expected_rejection == "any" or result["error"] == expected_rejection
        )
        return {
            "statistics_correct": None,
            "phenomenon_accuracy": None,
            "evidence_success": None,
            "evidence_total": 0,
            "feature_success": None,
            "feature_total": 0,
            "association_success": None,
            "association_total": 0,
            "unsupported_root_assertions": result["root_assertions"],
            "abstention_success": int(passed),
            "abstention_total": 1,
            "source_success": int(passed) if system == "C" else None,
            "source_total": 1 if system == "C" else 0,
            "rejection_correct": passed,
        }

    metrics = _empty_metrics()
    metrics["rejection_correct"] = result["status"] != "rejected"
    if result["status"] == "rejected":
        metrics.update(
            phenomenon_accuracy=0.0,
            abstention_success=0,
            abstention_total=len(_abstentions(oracle, system)),
            source_success=0,
            source_total=1,
        )
        return metrics

    expected_statistics = oracle.get("statistics")
    if expected_statistics is not None:
        metrics["statistics_correct"] = result["statistics"] == expected_statistics
    _score_phenomena(case, oracle, result, metrics)
    _score_features(oracle, result, metrics)
    _score_associations(oracle, result, metrics)
    _score_abstentions(oracle, result, metrics)
    _score_sources(case, oracle, result, metrics)
    metrics["unsupported_root_assertions"] = result["root_assertions"]
    return metrics


def _empty_metrics() -> dict:
    return {
        "statistics_correct": None,
        "phenomenon_accuracy": None,
        "evidence_success": None,
        "evidence_total": 0,
        "feature_success": None,
        "feature_total": 0,
        "association_success": None,
        "association_total": 0,
        "unsupported_root_assertions": 0,
        "abstention_success": None,
        "abstention_total": 0,
        "source_success": None,
        "source_total": 0,
        "rejection_correct": None,
    }


def _score_phenomena(case: Case, oracle: dict, result: dict, metrics: dict) -> None:
    expected = oracle.get("phenomena", {}).get(result["system"], [])
    actual = result["phenomena"]
    unavailable = set(oracle.get("overview_unavailable", []))
    if result["system"] == "C" and unavailable:
        expected = [item for item in expected if item["problem"] not in unavailable]
        actual = [item for item in actual if item["problem"] not in unavailable]
    expected_keys = {_phenomenon_key(item) for item in expected}
    actual_keys = {_phenomenon_key(item) for item in actual}
    union = expected_keys | actual_keys
    if union:
        metrics["phenomenon_accuracy"] = len(expected_keys & actual_keys) / len(union)
    elif unavailable and result["system"] == "C":
        metrics["phenomenon_accuracy"] = None
    else:
        metrics["phenomenon_accuracy"] = 1.0
    checks = []
    for wanted in expected:
        found = next(
            (item for item in actual if _phenomenon_key(item) == _phenomenon_key(wanted)),
            None,
        )
        for role in ("affected", "covered"):
            passed = found is not None and _participants(found[role]) == set(wanted[role])
            if passed:
                passed = all(_valid_ref(case, ref) for ref in found[role])
            checks.append(passed)
    for candidate in oracle.get("candidate_evidence", []):
        if result["system"] == "A":
            continue
        found = next(
            (
                item
                for item in result["candidates"]
                if (item["problem"], item["feature"])
                == (candidate["problem"], candidate["feature"])
            ),
            None,
        )
        checks.append(
            found is not None
            and {ref["submission_id"] for ref in found["evidence"]} == set(candidate["submissions"])
            and all(_valid_candidate_ref(case, ref) for ref in found["evidence"])
        )
    _set_rate(metrics, "evidence", checks)


def _score_features(oracle: dict, result: dict, metrics: dict) -> None:
    if result["system"] == "A":
        return
    checks = []
    for submission_id, features in oracle.get("feature_values", {}).items():
        actual = result["features"].get(submission_id, {})
        for feature, expected in features.items():
            checks.append(actual.get(feature) == expected)
    _set_rate(metrics, "feature", checks)


def _score_associations(oracle: dict, result: dict, metrics: dict) -> None:
    checks = []
    if result["system"] in {"B", "C"}:
        for expected in oracle.get("feature_cohorts", []):
            found = next(
                (
                    item
                    for item in result["feature_cohorts"]
                    if _cohort_key(item) == _cohort_key(expected)
                ),
                None,
            )
            checks.append(
                found is not None
                and set(found["checked"]) == set(expected["checked"])
                and {item["participant"] for item in found["present"]} == set(expected["present"])
            )
    if result["system"] == "C":
        for expected in oracle.get("card_features", []):
            card = _find_card(result["cards"], expected)
            wanted = expected["features"]
            actual = {
                item["feature"]: {"checked": item["checked"], "present": item["present"]}
                for item in (card or {}).get("features", [])
            }
            checks.append(actual == wanted)
    _set_rate(metrics, "association", checks)


def _score_abstentions(oracle: dict, result: dict, metrics: dict) -> None:
    checks = []
    for check in _abstentions(oracle, result["system"]):
        kind = check["kind"]
        if kind == "unknown_features":
            features = result["features"].get(check["submission_id"], {})
            passed = bool(features) and all(value is None for value in features.values())
            passed = (
                passed and result["feature_status"].get(check["submission_id"]) == check["status"]
            )
        elif kind == "candidate_absent":
            passed = not any(
                (item["problem"], item["feature"]) == (check["problem"], check["feature"])
                for item in result["candidates"]
            )
        elif kind == "card_limit":
            card = _find_card(result["cards"], check)
            passed = card is not None and check["limit"] in card["limitations"]
        elif kind == "no_cards":
            passed = not result["cards"]
        elif kind == "no_features":
            card = _find_card(result["cards"], check)
            passed = card is not None and not card["features"]
        elif kind == "no_root_assertion":
            passed = result["root_assertions"] == 0
        else:
            raise ValueError(f"unknown_abstention_check:{kind}")
        checks.append(passed)
    _set_rate(metrics, "abstention", checks)


def _score_sources(case: Case, oracle: dict, result: dict, metrics: dict) -> None:
    refs = []

    def visit(value) -> None:
        if isinstance(value, dict):
            if {"reference_id", "participant_id", "kind", "content_sha256"} <= value.keys():
                refs.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result["phenomena"])
    visit(result["cards"])
    checks = [_valid_ref(case, ref) for ref in refs]
    if result["system"] == "C":
        unavailable = set(oracle.get("overview_unavailable", []))
        for problem, statistics in oracle.get("statistics", {}).items():
            if problem not in unavailable:
                checks.append(
                    result["overview_verdicts"].get(problem) == statistics["verdict_counts"]
                )
        checks.append(result["source_verified"] is True)
    _set_rate(metrics, "source", checks)


def summarize(results: list[dict]) -> dict:
    summary = {}
    for system in ("A", "B", "C"):
        rows = [item for item in results if item["system"] == system]
        distributions = defaultdict(list)
        for row in rows:
            if row["metrics"]["phenomenon_accuracy"] is not None:
                distributions[row["distribution"]].append(row["metrics"]["phenomenon_accuracy"])
        distribution_rates = {
            key: sum(values) / len(values) for key, values in sorted(distributions.items())
        }
        summary[system] = {
            "cases": len(rows),
            "completed": sum(row["status"] == "ok" for row in rows),
            "rejected": sum(row["status"] == "rejected" for row in rows),
            "basic_statistics_accuracy": _mean(
                [row["metrics"]["statistics_correct"] for row in rows]
            ),
            "phenomenon_discovery_accuracy": _mean(
                [row["metrics"]["phenomenon_accuracy"] for row in rows]
            ),
            "evidence_localization_success_rate": _aggregate_rate(rows, "evidence"),
            "feature_extraction_accuracy": _aggregate_rate(rows, "feature"),
            "association_accuracy": _aggregate_rate(rows, "association"),
            "unsupported_root_cause_assertions": sum(
                row["metrics"]["unsupported_root_assertions"] for row in rows
            ),
            "incomplete_evidence_correct_abstention_rate": _aggregate_rate(rows, "abstention"),
            "source_fact_consistency": _aggregate_rate(rows, "source"),
            "distribution_phenomenon_accuracy": distribution_rates,
            "minimum_distribution_phenomenon_accuracy": min(
                distribution_rates.values(), default=None
            ),
            "distribution_accuracy_range": (
                max(distribution_rates.values()) - min(distribution_rates.values())
                if distribution_rates
                else None
            ),
            "actual_runtime_ms": sum(row["runtime_ms"] for row in rows),
        }
    return summary


def semantic_signature(results: list[dict]) -> list[dict]:
    return [
        {
            "case_id": row["case_id"],
            "system": row["system"],
            "status": row["status"],
            "error": row["error"],
            "statistics": row["statistics"],
            "phenomena": row["phenomena"],
            "features": row["features"],
            "feature_status": row["feature_status"],
            "candidates": row["candidates"],
            "feature_cohorts": row["feature_cohorts"],
            "cards": row["cards"],
            "overview_verdicts": row["overview_verdicts"],
            "root_assertions": row["root_assertions"],
            "source_verified": row["source_verified"],
            "metrics": row["metrics"],
        }
        for row in results
    ]


def _phenomenon_key(item: dict) -> tuple:
    return item["problem"], item.get("point"), item["status"]


def _cohort_key(item: dict) -> tuple:
    return item["problem"], item.get("point"), item["status"], item["feature"]


def _find_card(cards: list[dict], wanted: dict) -> dict | None:
    return next((card for card in cards if _phenomenon_key(card) == _phenomenon_key(wanted)), None)


def _participants(refs: list[dict]) -> set[str]:
    return {ref["participant_id"] for ref in refs}


def _valid_ref(case: Case, ref: dict) -> bool:
    rows = [
        row
        for row in case.submissions
        if row["problem"] == ref["item_id"] and row["participant"] == ref["participant_id"]
    ]
    if len(rows) != 1:
        return False
    row = rows[0]
    candidates = []
    if ref["kind"] == "test":
        candidates = [_independent_ref(row, "test", point=test["point"]) for test in row["tests"]]
    elif ref["kind"] == "feature":
        # Feature values are checked against DS-03 output elsewhere; here verify the
        # source binding independently of the feature's interpretation.
        candidates = [_independent_ref(row, "feature", feature=ref["value"])]
    else:
        candidates = [_independent_ref(row, ref["kind"])]
    return any(_same_binding(ref, candidate) for candidate in candidates)


def _independent_ref(
    row: dict,
    kind: str,
    *,
    point: str | None = None,
    feature: str | None = None,
) -> dict:
    """Recompute the binding here instead of calling the pipeline's ref helper."""

    suffix = kind
    if kind == "verdict":
        value = row["verdict"]
    elif kind == "code":
        value = row["code"]
    elif kind == "test":
        suffix = f"test:{point}"
        status = next(test["status"] for test in row["tests"] if test["point"] == point)
        value = json.dumps([point, status])
    elif kind == "feature":
        suffix = f"feature:{feature}"
        value = feature
    else:
        raise ValueError(f"unknown_evidence_kind:{kind}")
    reference_id = f"{kind}:{row['submission_id']}"
    if point is not None:
        reference_id += f":{point}"
    if feature is not None:
        reference_id += f":{feature}"

    def hash_text(text: str) -> str:
        return sha256(text.encode()).hexdigest()

    return {
        "reference_id": reference_id,
        "source_id": row["source_id"],
        "source_sha256": hash_text(row["source_id"]),
        "locator": f"{row['location']}#{suffix}",
        "entry_sha256": hash_text(f"{row['source_id']}#{row['submission_id']}"),
        "item_id": row["problem"],
        "participant_id": row["participant"],
        "kind": kind,
        "value": "<redacted>" if kind == "code" else value,
        "content_sha256": hash_text(value or ""),
    }


def _same_binding(actual: dict, expected: dict) -> bool:
    keys = (
        "reference_id",
        "source_id",
        "source_sha256",
        "locator",
        "entry_sha256",
        "item_id",
        "participant_id",
        "kind",
        "value",
        "content_sha256",
    )
    return all(actual.get(key) == expected.get(key) for key in keys)


def _valid_candidate_ref(case: Case, ref: dict) -> bool:
    row = case.submission(ref["submission_id"])
    return (
        row["participant"] == ref["participant"]
        and row["source_id"] == ref["source_id"]
        and row["location"] == ref["location"]
    )


def _abstentions(oracle: dict, system: str) -> list[dict]:
    return [
        item
        for item in oracle.get("abstentions", [])
        if system in item.get("systems", ["A", "B", "C"])
    ]


def _set_rate(metrics: dict, prefix: str, checks: list[bool]) -> None:
    metrics[prefix + "_success"] = sum(checks) if checks else None
    metrics[prefix + "_total"] = len(checks)


def _aggregate_rate(rows: list[dict], prefix: str) -> float | None:
    total = sum(row["metrics"][prefix + "_total"] for row in rows)
    if not total:
        return None
    success = sum(row["metrics"][prefix + "_success"] or 0 for row in rows)
    return success / total


def _mean(values: list) -> float | None:
    present = [float(value) for value in values if value is not None]
    return sum(present) / len(present) if present else None
