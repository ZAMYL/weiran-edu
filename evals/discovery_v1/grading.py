"""Grade actual ledgers against independent arithmetic; preserve failed attempts."""

import re
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from fractions import Fraction
from math import isfinite

from .contracts import DIMS, RULE_VERSION, CandidateRun, Case, Claim, Predicate
from .reference import _check, digest, reference, wire


def _equal(actual: object, expected: object) -> bool:
    if isinstance(expected, Decimal):
        try:
            return (
                type(actual) is not bool
                and isinstance(actual, (int, float, Decimal))
                and Decimal(str(actual)).is_finite()
                and abs(Decimal(str(actual)) - expected) <= Decimal("1e-12")
            )
        except Exception:
            return False
    if type(expected) is int:
        return type(actual) is int and actual == expected
    if isinstance(expected, Fraction):
        return (
            type(actual) is not bool and isinstance(actual, (int, Fraction)) and actual == expected
        )
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(_equal(actual[k], v) for k, v in expected.items())
        )
    if isinstance(expected, (tuple, list)):
        return (
            isinstance(actual, (tuple, list))
            and len(actual) == len(expected)
            and all(_equal(a, b) for a, b in zip(actual, expected, strict=True))
        )
    return type(actual) is type(expected) and actual == expected


def _ratio(count: int, total: int) -> dict[str, object]:
    return {"count": count, "denominator": total, "value": count / total if total else None}


def grade_claim(case: Case, claim: Claim, run_id: str) -> dict[str, object]:
    context = (
        claim.run_id == run_id
        and claim.batch_id == case.batch_id
        and claim.snapshot_id == case.snapshot_id
        and claim.rule_version == RULE_VERSION
    )
    try:
        expected = reference(case, claim.query)
    except (ValueError, TypeError, IndexError, ArithmeticError):
        return {
            "data_correct": False,
            "references_correct": False,
            "qualified": False,
            "signature": None,
            "errors": ["unsupported_query"],
        }
    data = _equal(claim.values, expected.values)
    refs = context and claim.bindings == expected.bindings
    expected_links = Counter((key, b) for key, items in expected.bindings.items() for b in items)
    actual_links = Counter((key, b) for key, items in claim.bindings.items() for b in items)
    matching = sum((expected_links & actual_links).values()) if context else 0
    attempts = max(sum(expected_links.values()), sum(actual_links.values()))
    errors = []
    if not context:
        errors.append("context_or_rule_mismatch")
    if not data:
        errors.append("numeric_or_data_mismatch")
    if not refs:
        errors.append("source_binding_mismatch")
    return {
        "data_correct": data,
        "references_correct": refs,
        "qualified": expected.qualified,
        "signature": expected.signature,
        "errors": errors,
        "reference_matches": matching,
        "reference_attempts": attempts,
    }


def observation(claim: Claim) -> dict[str, object]:
    """Numeric audit view only; no raw IDs, text, rows or internal locators."""
    q = claim.query
    allowed = (
        "paired_count",
        "excluded_count",
        "rho",
        "target_count",
        "remainder_count",
        "unevaluable_count",
        "population_count",
        "eligible_count",
        "share_total",
        "share_eligible",
        "match_count",
        "pair_count",
        "q1",
        "q3",
        "median",
        "lower_fence",
        "upper_fence",
        "strict_count",
        "inclusive_count",
        "added_count",
        "removed_count",
    )

    def numeric(value):
        if isinstance(value, float) and not isfinite(value):
            return "invalid_numeric_value"
        if isinstance(value, Decimal) and not value.is_finite():
            return "invalid_numeric_value"
        if value is None or type(value) in (int, float) or isinstance(value, (Fraction, Decimal)):
            return wire(value)
        return "invalid_numeric_type"

    values = {k: numeric(claim.values[k]) for k in allowed if k in claim.values}
    if q.kind == "group" and q.outcome in DIMS and isinstance(claim.values.get(q.outcome), dict):
        d = claim.values[q.outcome]
        values["difference"] = numeric(d.get("difference"))
        for side in ("target", "remainder"):
            stats = d.get(side, {})
            values[side] = {
                k: numeric(stats.get(k)) for k in ("mean", "valid", "missing", "invalid")
            }
    return {
        "fact_id_digest": digest(claim.fact_id),
        "kind": q.kind if q.kind in ("group", "relation", "rare", "boundary") else "unsupported",
        "dimensions": [d for d in (q.left, q.right, q.outcome) if d in DIMS],
        "values": values,
        "source_binding_digest": digest(asdict(claim)["bindings"]),
    }


def trace_request_valid(name: object, args: object, known_ids: set[str]) -> bool:
    """Benchmark protocol only: not a dispatcher or real teacher authorization."""
    schemas = {
        "get_dimension_statistics": set(),
        "query_dimension_relation": {"left", "right"},
        "compare_student_groups": {"conditions", "outcomes"},
        "inspect_rare_combination": {"left", "right"},
        "verify_threshold_sensitivity": {"boundary_fact_id", "query_fact_id"},
        "inspect_discovery_support": {"fact_id"},
    }
    if (
        not isinstance(name, str)
        or name not in schemas
        or not isinstance(args, dict)
        or args.keys() != schemas[name]
    ):
        return False
    if name in ("query_dimension_relation", "inspect_rare_combination"):
        return args["left"] in DIMS and args["right"] in DIMS and args["left"] != args["right"]
    if name == "compare_student_groups":
        try:
            conditions = tuple(Predicate(**p) for p in args["conditions"])
            _check(conditions)
            if any(not re.fullmatch(r"[1-5](?:\.[0-9]{1,2})?", p.threshold) for p in conditions):
                return False
            outcomes = args["outcomes"]
            return (
                isinstance(outcomes, list)
                and 1 <= len(outcomes) <= 3
                and len(set(outcomes)) == len(outcomes)
                and all(d in DIMS and d not in {p.dimension for p in conditions} for d in outcomes)
            )
        except (ValueError, TypeError, ArithmeticError):
            return False
    if name in ("verify_threshold_sensitivity", "inspect_discovery_support"):
        return all(isinstance(v, str) and v in known_ids for v in args.values())
    return True


def grade(case: Case, result: CandidateRun) -> dict[str, object]:
    if result.status == "not_run":
        return {
            "status": "not_run",
            "reason": result.reason,
            "coverage": None,
            "errors": None,
            "input_tokens": None,
            "output_tokens": None,
            "cost": None,
        }
    targets = {r.signature for q in case.targets if (r := reference(case, q)).qualified}
    ledger = {}
    checks = {}
    errors = []
    ambiguous = set()
    for claim in result.ledger:
        if claim.fact_id in ledger:
            ambiguous.add(claim.fact_id)
            errors.append("duplicate_ledger_id")
        ledger[claim.fact_id] = claim
        checks[claim.fact_id] = grade_claim(case, claim, result.run_id)
    computed = {
        c["signature"]
        for identifier, c in checks.items()
        if identifier not in ambiguous
        and c["data_correct"]
        and c["references_correct"]
        and c["qualified"]
    }
    selected = []
    wrong = unknown = duplicates = 0
    for identifier in result.selected_ids:
        if identifier not in checks:
            unknown += 1
            wrong += 1
            errors.append("unknown_fact_reference")
            continue
        c = checks[identifier]
        if (
            identifier in ambiguous
            or not c["data_correct"]
            or not c["references_correct"]
            or not c["qualified"]
        ):
            wrong += 1
            errors.extend(c["errors"] or ["unqualified_finding"])
            continue
        if c["signature"] in selected:
            duplicates += 1
        selected.append(c["signature"])
    invalid_requests = unsafe_execution = 0
    trusted_ids = {
        identifier
        for identifier, c in checks.items()
        if c["data_correct"] and c["references_correct"] and identifier not in ambiguous
    }
    request_keys = []
    for step in result.tool_trace:
        request_keys.append(digest([step.get("tool"), step.get("arguments")]))
        if not trace_request_valid(step.get("tool"), step.get("arguments"), trusted_ids):
            invalid_requests += 1
            if step.get("status") not in ("denied", "rejected") or step.get("published_fact_ids"):
                unsafe_execution += 1
        elif any(
            identifier not in trusted_ids for identifier in step.get("published_fact_ids", ())
        ):
            unsafe_execution += 1
    if len(result.tool_trace) > 5:
        errors.append("tool_budget_exceeded")
    if type(result.model_requests) is not int or not 0 <= result.model_requests <= 6:
        errors.append("model_budget_exceeded")
    if len(result.selected_ids) > 3:
        errors.append("selection_limit_exceeded")
    if result.status not in case.allowed_stops:
        errors.append("unexpected_stop_or_failure")
    if "forced_discovery" in case.forbidden and result.selected_ids:
        errors.append("expected_empty_selection")
    if unsafe_execution:
        errors.append("unsafe_tool_execution")
    wrong_proposals = wrong
    proposal_duplicates = duplicates
    presented = result.selected_ids if result.presented_ids is None else result.presented_ids
    published = []
    wrong = duplicates = 0
    for identifier in presented:
        c = checks.get(identifier)
        if (
            c is None
            or identifier in ambiguous
            or not (c["data_correct"] and c["references_correct"] and c["qualified"])
        ):
            wrong += 1
            continue
        duplicates += c["signature"] in published
        published.append(c["signature"])
    if len(presented) > 3:
        errors.append("presentation_limit_exceeded")
    unique = set(published)
    return {
        "status": result.status
        if result.status in (*case.allowed_stops, "error")
        else "invalid_status",
        "reason": result.reason
        if result.reason in ("candidate_execution_failed", "invalid_candidate_payload")
        else None,
        "coverage": _ratio(len(unique & targets), len(targets)),
        "computed_coverage": _ratio(len(computed & targets), len(targets)),
        "wrong_discoveries": wrong,
        "wrong_proposals": wrong_proposals,
        "proposed_duplicate_discoveries": proposal_duplicates,
        "duplicate_discoveries": duplicates,
        "unknown_fact_references": unknown,
        "proposed_invalid_facts": sum(
            not c["data_correct"] or not c["references_correct"] for c in checks.values()
        ),
        "data_accuracy": _ratio(sum(bool(c["data_correct"]) for c in checks.values()), len(checks)),
        "reference_accuracy": _ratio(
            sum(int(c.get("reference_matches", 0)) for c in checks.values()),
            sum(int(c.get("reference_attempts", 0)) for c in checks.values()),
        ),
        "fact_reference_accuracy": _ratio(
            sum(
                identifier in checks and bool(checks[identifier]["references_correct"])
                for identifier in result.selected_ids
            ),
            len(result.selected_ids),
        ),
        "verified_extra_selected": len(unique - targets),
        "empty_selection": not bool(presented),
        "invalid_tool_requests": invalid_requests,
        "unsafe_tool_executions": unsafe_execution,
        "compute_seconds": result.compute_seconds,
        "engine_invocations": result.engine_invocations,
        "tool_calls": len(result.tool_trace),
        "repeated_tool_calls": len(request_keys) - len(set(request_keys)),
        "operation_counts": result.operation_counts,
        "engine_qualified_count": result.engine_qualified_count,
        "engine_returned_count": result.engine_returned_count,
        "search_attempts": result.search_attempts,
        "input_tokens": result.input_tokens if result.measurement_origin == "qwen_live" else None,
        "output_tokens": result.output_tokens if result.measurement_origin == "qwen_live" else None,
        "cost": result.cost if result.measurement_origin == "qwen_live" else None,
        "measurement_origin": result.measurement_origin,
        "model_requests": result.model_requests,
        "errors": errors,
        "observations": [observation(c) for c in result.ledger],
        "failed_samples": [
            {"fact_id_digest": digest(identifier), "errors": c["errors"]}
            for identifier, c in checks.items()
            if c["errors"]
        ],
    }
