"""Read-only dry-run; legacy paid execution is unconditionally disabled."""

import argparse
import json
from dataclasses import asdict

from ..analysis import Kernel
from ..planners import bound_schemas
from ..synthetic import admit
from .pricing import PRICE
from .safety import LIVE_DISABLED, deny_live

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
            "Live disabled: requires separately reviewed code-bound service and shared ledger",
        ],
        "no_credentials_read": True,
    }


async def execute(recipe, authorization_path):
    """Compatibility entry, closed before reading any authorization or credentials."""
    deny_live()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", choices=RECIPES, default="five")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="disabled pending exact-code independent review and a shared Live ledger",
    )
    parser.add_argument(
        "--authorization",
        help="local operator authorization, never a teacher data file",
    )
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(preflight(args.recipe), ensure_ascii=False, indent=2))
        return 0
    parser.error(LIVE_DISABLED)


if __name__ == "__main__":
    raise SystemExit(main())
