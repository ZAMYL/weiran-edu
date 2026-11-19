"""Offline commands only: produce a rule report or plan a reviewable campaign."""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from unittest.mock import patch

from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261112

from app.ai.edu.material_profile import _header_tokens

from .runner import cases, dataset_digest, reviewed_payloads, run_rules


def estimate(selected: list[dict], output_limit: int, *, cap_cny: Decimal) -> dict:
    price = REVIEWED_PRICE_20261112
    today = datetime.now(UTC).date()
    price_current = price.checked_on <= today.isoformat() <= price.valid_until
    per_call = price.max_input_tokens + output_limit
    upper = price.cost(price.max_input_tokens, output_limit) * len(selected)
    return {
        "origin": "budget_preflight_only",
        "live_model": {"status": "blocked", "quality": None, "calls": None},
        "blockers": [
            "new_paid_and_externalization_approval_required",
            "evaluation_payloads_not_registered_with_existing_live_admission",
            *([] if price_current else ["reviewed_price_needs_refresh"]),
            *([] if upper <= cap_cny else ["budget_cap_insufficient"]),
            *([] if 0 < len(selected) <= 6 else ["request_budget_exceeded"]),
        ],
        "model": price.model,
        "price_id": price.price_id,
        "price_valid_until": price.valid_until,
        "price_current_utc": price_current,
        "dataset_sha256": dataset_digest(),
        "payload_sha256": reviewed_payloads(selected),
        "planned_calls": len(selected),
        "request_budget_ok": 0 < len(selected) <= 6,
        "max_input_tokens_each": price.max_input_tokens,
        "max_output_tokens_each": output_limit,
        "required_token_reservation": per_call * len(selected),
        "estimated_upper_bound_cny": str(upper),
        "configured_max_cny": str(cap_cny),
        "budget_ok": upper <= cap_cny and 0 < len(selected) <= 6,
    }


def compare_headers() -> dict:
    def stats(report):
        metrics = report["metrics"]
        keys = (
            "fields",
            "role_exact_accuracy",
            "links",
            "unknown_total",
            "unknown_abstained",
            "unsubstantiated",
        )
        return {key: metrics[key] for key in keys}

    after = run_rules("all")
    holdout_after = run_rules("holdout")
    with patch(
        "app.ai.edu.material_profile._header_tokens", partial(_header_tokens, aliases=False)
    ):
        before = run_rules("all")
        holdout_before = run_rules("holdout")
    return {
        "before": stats(before),
        "after": stats(after),
        "holdout_before": stats(holdout_before),
        "holdout_after": stats(holdout_after),
        "masked_before": sum(len(c["masked_gold_fields"]) for c in before["cases"]),
        "masked_after": sum(len(c["masked_gold_fields"]) for c in after["cases"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("rules", "budget", "compare-headers"))
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    parser.add_argument("--case", action="append", help="explicit budget review case ID")
    parser.add_argument("--output-tokens", type=int, default=800)
    parser.add_argument("--cap-cny", type=Decimal, default=Decimal("2.40"))
    args = parser.parse_args()
    if not 1 <= args.output_tokens <= 800 or not args.cap_cny.is_finite() or args.cap_cny <= 0:
        parser.error("invalid budget")
    selected = cases(args.split)
    if args.case:
        if args.mode != "budget":
            parser.error("--case is only supported by budget preflight")
        wanted = set(args.case)
        selected = [case for case in selected if case["id"] in wanted]
        if len(wanted) != len(selected):
            parser.error("unknown case ID")
    if args.mode == "rules":
        report = run_rules(args.split)
    elif args.mode == "compare-headers":
        if args.split != "all":
            parser.error("compare-headers always reports both all and holdout")
        report = compare_headers()
    else:
        report = estimate(selected, args.output_tokens, cap_cny=args.cap_cny)
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
