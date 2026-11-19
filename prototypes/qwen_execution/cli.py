"""Dry-run by default; execution requires an exact-code reviewed operator approval file."""

import argparse
import asyncio
import json
from pathlib import Path

from app.ai.edu.qwen_config import QwenSettings

from prototypes.adaptive_assessment.live.budget import ledger_root
from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261119
from prototypes.adaptive_assessment.synthetic import admit

from .runtime import (
    ROOT,
    ReviewedApproval,
    ReviewedBudget,
    code_fingerprint,
    run_reviewed_profiles,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", default="profile-balanced-72")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    source = admit(args.recipe)
    price = REVIEWED_PRICE_20261119
    # Server-owned: no CLI flag, working directory or report path may pick the ledger.
    root = ledger_root()
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "dry_run_no_request",
                    "source_fingerprint": source.fingerprint,
                    "reviewed_code_sha256": code_fingerprint(),
                    "ledger_root": str(root),
                    "model": price.model,
                    "price_fingerprint": price.fingerprint,
                    "price_valid_until": price.valid_until,
                    "max_requests": 6,
                    "max_tool_attempts": 5,
                    "max_output_tokens": 800,
                    "conservative_six_request_cost_cny": str(
                        6 * price.cost(price.max_input_tokens, 800)
                    ),
                    "real_qwen": "not_run",
                    "teacher_api_enabled": False,
                },
                indent=2,
            )
        )
        return
    if args.approval is None:
        parser.error("execution_requires_a_reviewed_approval_file")
    if root == ROOT or root.is_relative_to(ROOT):
        parser.error("ledger_root_must_be_outside_the_repository")
    raw = args.approval.read_bytes()
    approval = ReviewedApproval.model_validate_json(raw)

    def authorize():
        if args.approval.read_bytes() != raw:
            raise ValueError("approval_revoked_or_changed")

    # Check approval before loading any credentials or constructing any network client.
    from datetime import UTC, datetime

    approval.check(datetime.now(UTC).date())
    settings = QwenSettings.from_env()
    budget = ReviewedBudget(approval, authorize=authorize)
    try:
        result, _, _ = asyncio.run(run_reviewed_profiles(args.recipe, budget, settings))
        # Excludes outbound payloads, source objects, row witnesses and credentials.
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "status",
                        "stop_reason",
                        "origin",
                        "real_qwen",
                        "model_choices",
                        "tool_ledger",
                        "trace",
                        "model_requests",
                        "tool_attempts",
                        "profiles",
                        "actual_tokens",
                        "actual_cost_cny",
                        "budget",
                        "source_replay_verified",
                        "elapsed_total_seconds",
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        budget.close()


if __name__ == "__main__":
    main()
