"""Independent numeric/source/disclosure scoring of actually issued native facts."""

import json
from collections import Counter
from decimal import Decimal
from fractions import Fraction as F
from hashlib import sha256

from evals.discovery_v1.reference import digest, reference

from .reference import expectation, frozen_query, signature, targets


def equal(actual, expected):
    if isinstance(expected, Decimal):
        return (
            type(actual) in (int, float, Decimal)
            and Decimal(str(actual)).is_finite()
            and abs(Decimal(str(actual)) - expected) <= Decimal("1e-12")
        )
    if isinstance(expected, F):
        if isinstance(actual, dict) and set(actual) in (
            {"numerator", "denominator"},
            {"numerator", "denominator", "display"},
        ):
            if (
                type(actual["numerator"]) is not int
                or type(actual["denominator"]) is not int
                or actual["denominator"] <= 0
            ):
                return False
            rational = F(actual["numerator"], actual["denominator"])
            if "display" in actual and actual["display"] != f"{float(rational):.4f}":
                return False
            actual = rational
        return type(actual) is not bool and isinstance(actual, (int, F)) and actual == expected
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(equal(actual[k], v) for k, v in expected.items())
        )
    if isinstance(expected, (list, tuple)):
        return (
            isinstance(actual, (list, tuple))
            and len(actual) == len(expected)
            and all(equal(a, b) for a, b in zip(actual, expected, strict=True))
        )
    return type(actual) is type(expected) and actual == expected


def pid(spec, i):
    return f"synthetic-member-{i:04}" if spec.items[0].startswith("I") else f"S{i:04}"


def source_check(spec, executor, fact, expected):
    errors = []
    actual = {s.key: s for s in fact.item_sources}
    if len(actual) != len(fact.item_sources) or set(actual) != set(expected):
        errors.append("metric_source_keys_mismatch")
    digestible = []
    for key, (item, indexes, kind) in expected.items():
        s = actual.get(key)
        members = tuple(pid(spec, i) for i in indexes)
        if (
            s is None
            or s.item_id != item
            or s.participants != members
            or len(s.references) != len(members)
        ):
            errors.append("actual_participants_mismatch")
            continue
        for i, member, ref in zip(indexes, members, s.references, strict=True):
            w = executor.kernel.witnesses.get(ref)
            value = spec.rows[i][spec.items.index(item)]
            raw = (
                None
                if value is None
                else format(Decimal(value.numerator) / Decimal(value.denominator), "f")
            )
            if spec.items[0].startswith("I"):
                from evals.discovery_v1.contracts import DIMS

                dim = DIMS[spec.items.index(item)]
                locator = f"memory-row:{i}; original:synthetic-score-{i:04}-{dim}"
            else:
                locator = f"answers/{member}.html#{item}" + (".score" if kind == "score" else "")
            content = (
                (raw or "")
                if kind == "score"
                else json.dumps([raw, "absent" if item in spec.absent else "available"])
            )
            if (
                w is None
                or ref != f"{kind}-{member}-{item}"
                or w.kind != kind
                or w.item_id != item
                or w.participant_id != member
                or w.source_id != "synthetic-source-" + executor.source.fingerprint
                or w.source_sha256 != executor.source.fingerprint
                or w.locator != locator
                or w.value != (raw if kind == "score" else None)
                or w.content_sha256 != sha256(content.encode()).hexdigest()
            ):
                errors.append("witness_scope_content_or_locator_mismatch")
        digestible.append([key, item, kind, digest(members), len(members)])
    return not errors, sorted(set(errors)), digest(digestible)


def summary_expected(spec, fact, released):
    v = fact.view
    if set(v) != {"fact_id", "qualified", "items", "state"} or v["qualified"] is not False:
        raise ValueError("invalid_summary_contract")
    requested = fact.query["arguments"].get("item_ids")
    ids = spec.items if requested is None else tuple(i for i in spec.items if i in requested)
    if [i["item_id"] for i in v["items"]] != list(ids):
        raise ValueError("summary_item_scope_mismatch")
    sources = {}
    staged = []
    for item in v["items"]:
        identifier = item["item_id"]
        stats = spec.stats(identifier)
        candidates = [frozenset(spec.valid(identifier))] + [
            frozenset(i for i in range(len(spec.rows)) if spec.state(i, identifier) == state)
            for state in stats["states"]
        ]
        must_suppress = stats["denominator"] < 5
        pending = []
        for members in candidates:
            must_suppress |= 0 < len(members) < 5 or any(
                0 < len(members ^ old) < 5 for old in (*released, *staged, *pending)
            )
            pending.append(members)
        if item.get("state") == "privacy_or_data_limited":
            if not must_suppress:
                raise ValueError("unjustified_summary_suppression")
            if set(item) != {"item_id", "state"}:
                raise ValueError("private_summary_leak")
            continue
        if must_suppress:
            raise ValueError("unsuppressed_small_difference")
        staged += pending
        expected = {
            "item_id": identifier,
            **stats,
            "state": "available" if stats["valid_count"] >= 5 else "no_authorized_numeric_data",
            "has_variance": len(set(spec.valid(identifier).values())) > 1,
        }
        counts = Counter(spec.valid(identifier).values())
        expected["exploration_thresholds"] = [
            format(Decimal(x.numerator) / Decimal(x.denominator), "f")
            for x, count in sorted(counts.items())
            if count >= 5 and (count == stats["valid_count"] or stats["valid_count"] - count >= 5)
        ][:8]
        for key, value in expected.items():
            if not equal(item.get(key), value):
                raise ValueError("summary_numeric_or_state_mismatch")
        sources[identifier + ".valid"] = (identifier, tuple(spec.valid(identifier)), "score")
        for state in stats["states"]:
            sources[identifier + ".state." + state] = (
                identifier,
                tuple(i for i in range(len(spec.rows)) if spec.state(i, identifier) == state),
                "row",
            )
        allowed = set(expected)
        if "coarse_distribution" in item:
            scale = spec.scales[spec.items.index(identifier)]
            if scale is None:
                raise ValueError("unknown_scale_distribution")
            low, high, _ = scale
            bins = []
            for k in range(4):
                left, right = low + (high - low) * k / 4, low + (high - low) * (k + 1) / 4
                indexes = tuple(
                    i
                    for i, v in spec.valid(identifier).items()
                    if left <= v and (v <= right if k == 3 else v < right)
                )
                bins.append(
                    {
                        "lower": left,
                        "upper": right,
                        "upper_inclusive": k == 3,
                        "count": len(indexes),
                    }
                )
                sources[f"{identifier}.bin{k}"] = (identifier, indexes, "score")
            if not equal(item["coarse_distribution"], bins):
                raise ValueError("distribution_mismatch")
            allowed.add("coarse_distribution")
        if "distribution_state" in item:
            if item["distribution_state"] != "privacy_limited":
                raise ValueError("invalid_distribution_state")
            allowed.add("distribution_state")
        if set(item) != allowed:
            raise ValueError("unknown_summary_fields")
    if v["state"] != ("available" if len(spec.rows) >= 5 else "insufficient_data"):
        raise ValueError("summary_population_state")
    return sources


def frozen_projection_check(spec, fact):
    """Reuse exact PR47 values, without inventing its unreturned all-dimension Claim."""
    q = frozen_query(spec, fact.query["tool"], fact.query["arguments"])
    ref = reference(spec.frozen, q)
    v = fact.calculation.values
    if q.kind == "relation":
        projected = {k: v[k] for k in ("paired_count", "excluded_count", "state", "rho")}
        return equal(projected, ref.values), ref.signature, ref.qualified
    expected = {
        "target_count": ref.values["target_count"],
        "remainder_count": ref.values["remainder_count"],
        "unevaluable_count": ref.values["unevaluable_count"],
        "denominator": ref.values["population_count"],
        "eligible_denominator": ref.values["eligible_count"],
        "share_total": ref.values["share_total"],
        "share_eligible": ref.values["share_eligible"],
        "target_mean": ref.values[q.outcome]["target"]["mean"],
        "remainder_mean": ref.values[q.outcome]["remainder"]["mean"],
        "difference": ref.values[q.outcome]["difference"],
        "target_valid": ref.values[q.outcome]["target"]["valid"],
        "remainder_valid": ref.values[q.outcome]["remainder"]["valid"],
    }
    return all(equal(v[k], value) for k, value in expected.items()), ref.signature, ref.qualified


def grade_native(spec, collected):
    run, e = collected.run, collected.executor
    checks = {}
    released = [frozenset(range(len(spec.rows)))]
    disclosure_errors = []
    ids = {pid(spec, i): i for i in range(len(spec.rows))}
    for f in run.facts:
        errors = []
        expected_sources = {}
        data_ok = True
        qualified = False
        sem = None
        try:
            if (
                f.snapshot_id != e.source.fingerprint
                or f.fact_id not in e.facts
                or not f.fact_id.startswith(run.run_id + "-f")
            ):
                raise ValueError("context_or_fact_issuer_mismatch")
            e._verify(f)  # Runtime proof replay AND independent arithmetic below.
            if f.tool == "get_dimension_statistics":
                expected_sources = summary_expected(spec, f, released)
            elif f.tool == "inspect_discovery_support":
                parent = e.facts.get(f.query["arguments"]["fact_id"])
                parent_check = checks.get(parent.fact_id) if parent else None
                if (
                    not parent_check
                    or not parent_check["data_correct"]
                    or not parent_check["sources_correct"]
                ):
                    raise ValueError("unverified_support_parent")
                expected_q = "qualified" if parent_check["qualified"] else "investigation_only"
                if (
                    f.view["checked_fact_id"] != parent.fact_id
                    or f.view["qualification"] != expected_q
                ):
                    raise ValueError("support_qualification_mismatch")
                expected_sources = parent_check["expected_sources"]
            else:
                expected, expected_sources, qualified = expectation(
                    spec, f.tool, f.query["arguments"]
                )
                data_ok = (
                    equal(f.calculation.values, expected) and f.calculation.qualified == qualified
                )
                data_ok = (
                    data_ok
                    and f.view.get("qualified") == qualified
                    and equal(f.view["metrics"], expected)
                )
                if spec.frozen:
                    frozen_ok, sem, freeze_qual = frozen_projection_check(spec, f)
                    data_ok = data_ok and frozen_ok and freeze_qual == qualified
                else:
                    sem = signature(spec, f.tool, f.query["arguments"])
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            data_ok = False
            errors.append(str(exc) if type(exc) is ValueError else "fact_verification_failed")
        source_ok, source_errors, proof_digest = source_check(spec, e, f, expected_sources)
        errors += source_errors
        # Independent accumulation includes all same-tool sets and previously published facts.
        pending = []
        for source in f.item_sources:
            try:
                members = frozenset(ids[p] for p in source.participants)
            except KeyError:
                disclosure_errors.append("foreign_disclosure_scope")
                continue
            if 0 < len(members) < 5 or any(
                0 < len(members ^ old) < 5 for old in (*released, *pending)
            ):
                disclosure_errors.append("cumulative_difference_disclosure")
            pending.append(members)
        released += pending
        checks[f.fact_id] = {
            "data_correct": data_ok,
            "sources_correct": source_ok,
            "qualified": qualified,
            "signature": sem,
            "errors": sorted(set(errors)),
            "source_digest": proof_digest,
            "expected_sources": expected_sources,
        }
    id_counts = Counter(f.fact_id for f in run.facts)
    for identifier, n in id_counts.items():
        if n > 1:
            checks[identifier]["data_correct"] = False
            checks[identifier]["sources_correct"] = False
            checks[identifier]["errors"].append("ambiguous_ledger_fact_id")
    valid = {i for i, c in checks.items() if c["data_correct"] and c["sources_correct"]}
    qualified = {i for i in valid if checks[i]["qualified"]}
    all_targets = targets(spec)
    proposed = tuple(
        i
        for proposal in collected.audit["model_proposals"]
        if proposal["decision"].get("name") == "finish"
        for i in proposal["decision"].get("arguments", {}).get("selected_fact_ids", [])
    )
    if not proposed:
        proposed = run.proposed_ids
    selected = run.selected_ids
    presented = set(selected)
    card_ids = [card.get("fact_id") for card in run.cards]
    projection_errors = []
    bad_cards = set()
    if card_ids != list(selected):
        projection_errors.append("card_selection_mismatch")
    for card in run.cards:
        identifier = card.get("fact_id")
        original = e.facts.get(identifier)
        if (
            identifier not in qualified
            or original is None
            or original.calculation is None
            or card.get("batch_id") != e.source.envelope.data.batch.batch_id
            or card.get("snapshot_id") != e.source.fingerprint
            or card.get("rule_version") != "edu-adaptive-prototype-0.1-draft"
            or not equal(card.get("metrics"), original.calculation.values)
            or not equal(card.get("chart"), original.calculation.values)
        ):
            bad_cards.add(identifier)
            projection_errors.append("unverified_card_metrics_or_context")
    selected_signatures = [
        checks[i]["signature"]
        for i in card_ids
        if i in qualified and i in selected and i not in bad_cards
    ]
    computed = {checks[i]["signature"] for i in qualified}
    covered = set(selected_signatures) & all_targets
    false = len({i for i in selected if i not in qualified} | bad_cards)
    invalid_proposals = sum(
        not p["legal_domain_request"] for p in collected.audit["model_proposals"]
    )
    success = run.status == "completed"
    expected_empty = (
        spec.frozen is not None
        and "forced_discovery" in spec.frozen.forbidden
        or spec.key in ("constant17", "small", "empty")
    )
    budget = collected.audit["budget"]
    return {
        "status": run.status,
        "reason": run.reason,
        "executed": True,
        "coverage": {"count": len(covered), "denominator": len(all_targets)},
        "computed_coverage": {
            "count": len(computed & all_targets),
            "denominator": len(all_targets),
        },
        "missed_targets": sorted(all_targets - set(selected_signatures)),
        "false_discoveries": false,
        "duplicate_discoveries": len(selected_signatures) - len(set(selected_signatures)),
        "card_projection_errors": sorted(set(projection_errors)),
        "duplicate_final_proposals": len(proposed)
        - len(set(checks[i]["signature"] if i in qualified else i for i in proposed)),
        "wrong_final_proposals": sum(i not in qualified for i in proposed),
        "data_accuracy": {
            "count": sum(checks[f.fact_id]["data_correct"] for f in run.facts),
            "denominator": len(run.facts),
        },
        "source_accuracy": {
            "count": sum(checks[f.fact_id]["sources_correct"] for f in run.facts),
            "denominator": len(run.facts),
        },
        "illegal_tool_proposals": invalid_proposals,
        "disclosure_errors": sorted(set(disclosure_errors)),
        "zero_finding_correct": (success and not presented) if expected_empty else None,
        "verified_extra_selected": len(set(selected_signatures) - all_targets),
        "actual_tokens": None,
        "actual_cost_cny": None,
        "http_usage_fixture": {
            k: budget[k] for k in ("request_count", "charged_tokens", "charged_cny_upper_bound")
        }
        if budget
        else None,
        "model_requests": collected.audit["model_requests"],
        "tool_attempts": run.tool_attempts,
        "elapsed_seconds": collected.audit["elapsed_seconds"],
        "operation_counts": collected.audit["operation_counts"],
        "fact_checks": [
            {"fact_id": i, **{k: v for k, v in c.items() if k != "expected_sources"}}
            for i, c in checks.items()
        ],
        "passed": success
        and (not expected_empty or not presented)
        and not false
        and not projection_errors
        and not disclosure_errors
        and len(covered) == len(all_targets)
        and all(c["data_correct"] and c["sources_correct"] for c in checks.values()),
    }
