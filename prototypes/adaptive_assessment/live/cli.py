"""Default dry-run; only explicit local operator authorization can create a live client."""

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path

from app.ai.edu.discovery.models import AgentError
from app.ai.edu.models import InsightError
from pydantic import ValidationError

from ..analysis import Kernel
from ..planners import bound_schemas
from ..synthetic import admit
from .budget import CampaignBudget, SyntheticLiveAuthorization
from .planner import render_result, run_synthetic_live
from .pricing import PRICE

RECIPES = (
    "five",
    "constant17",
    "contest13",
    "contest13_complete",
    "limited",
    "no_evidence",
    "small",
    "empty",
)


def preflight(recipe):
    source = admit(recipe)
    source.validate()
    catalog = Kernel(source.envelope).catalog()
    return {
        "status": "dry_run_no_network",
        "synthetic_only": True,
        "production_enabled": False,
        "source_fingerprint": source.fingerprint,
        "model": PRICE.model,
        "reviewed_price": asdict(PRICE),
        "price_fingerprint": PRICE.fingerprint,
        "reservation": {
            "input_tokens": PRICE.max_input_tokens,
            "output_tokens": 800,
            "total_tokens": PRICE.max_input_tokens + 800,
            "cny_upper_bound": str(PRICE.cost(PRICE.max_input_tokens, 800)),
        },
        "capabilities": catalog,
        "tools": [
            {
                "type": "function",
                "function": {"name": name, "parameters": schema.model_json_schema()},
            }
            for name, schema in bound_schemas(catalog).items()
        ],
        "before_execute": [
            "review and merge stacked PRs",
            "renew expired reviewed price",
            "separate human scenario/model/request/token/CNY authorization",
            "local authorization JSON bound to endpoint hash and source fingerprints",
            "server environment key/base URL",
            "explicit --execute --authorization PATH",
        ],
        "no_credentials_read": True,
    }


async def execute(recipe, authorization_path):
    path = Path(authorization_path)
    if path.stat().st_size > 16384:
        raise AgentError("authorization_file_limit")
    authorization = SyntheticLiveAuthorization.model_validate_json(path.read_bytes())
    state = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "state")
    budget = CampaignBudget(
        authorization,
        state / "weiran-edu" / "adaptive-live-budgets",
        origin="synthetic_live",
    )
    try:
        return await run_synthetic_live(recipe, budget, allow_network=True)
    except asyncio.CancelledError:
        return {"status": "cancelled", "cards": [], "budget": budget.snapshot()}
    except (AgentError, InsightError) as exc:
        return {
            "status": "failed",
            "reason": str(exc),
            "cards": [],
            "budget": budget.snapshot(),
        }
    finally:
        budget.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", choices=RECIPES, default="five")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="paid calls, separate human approval required",
    )
    parser.add_argument(
        "--authorization",
        help="local operator authorization, never a teacher data file",
    )
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(preflight(args.recipe), ensure_ascii=False, indent=2))
        return 0
    if not args.authorization:
        parser.error("--execute requires --authorization and separate human approval")
    try:
        result = asyncio.run(execute(args.recipe, args.authorization))
    except (ValidationError, OSError, ValueError):
        result = {
            "status": "failed",
            "reason": "invalid_local_authorization_or_configuration",
            "cards": [],
        }
    print(render_result(result))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
