"""Real Function Calling HTTP Mock, or explicit separately authorized Synthetic Live."""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from app.ai.edu.discovery.models import AgentError, Limits
from pydantic import ValidationError

from prototypes.adaptive_assessment.live.budget import CampaignBudget, SyntheticLiveAuthorization
from prototypes.adaptive_assessment.live.demo import MOCK_DATE, mock_authorization, native_response
from prototypes.adaptive_assessment.planners import AdaptiveFake

from .runtime import investigate, markdown, technical_markdown
from .synthetic import RECIPES


class ResearchMock:
    """First legal pending direction, explicitly a fixture policy, not model reasoning."""

    def choose(self, view: dict) -> dict:
        coverage = view["reflection"]["coverage"]
        if coverage["can_continue"]:
            pending = [d for d in coverage["directions"] if d["status"] == "uninvestigated"]
            if pending:
                direction = pending[0]
                return {
                    "name": direction["tool"],
                    "arguments": direction["arguments"],
                    "basis_fact_ids": direction["basis_fact_ids"],
                }
        selected = [f["fact_id"] for f in view["facts"] if f["qualified"]]
        return {
            "name": "finish",
            "arguments": {
                "selected_fact_ids": selected[:3],
                "reason": "completed" if selected else "no_valid_findings",
            },
        }


def mock_transport(policy=None, fault=None):
    policy = policy or ResearchMock()
    count = 0

    def handler(request):
        nonlocal count
        payload = json.loads(request.content)
        view = json.loads(payload["messages"][-1]["content"])
        choice = policy.choose(view)
        if fault:
            choice = fault(choice, view)
        response = native_response(choice, count)
        count += 1
        return httpx.Response(200, json=response)

    return httpx.MockTransport(handler)


async def offline(
    recipe: str,
    question: str,
    state: Path,
    *,
    fixed=False,
    limits: Limits | None = None,
    fault=None,
    access="synthetic_full",
):
    budget = CampaignBudget(
        mock_authorization(recipe, campaign_id=("fixed-" if fixed else "agent-") + recipe),
        state,
        origin="http_mock",
        today=MOCK_DATE,
    )
    try:
        return await investigate(
            recipe,
            question,
            budget,
            limits=limits,
            transport=mock_transport(AdaptiveFake() if fixed else None, fault),
            access=access,
        )
    finally:
        budget.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", choices=tuple(RECIPES), default="research-v2-capability")
    parser.add_argument("--question", default="比较 I01、I02 的组合与 I03 的群体差异")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorization", type=Path)
    args = parser.parse_args()
    if any(
        p.exists()
        for p in (
            args.output,
            args.output.with_suffix(".md"),
            args.output.with_suffix(".technical.md"),
        )
    ):
        parser.error("new_output_paths_required")
    if args.execute and not args.authorization:
        parser.error("separate human authorization and --authorization required")

    async def run():
        with TemporaryDirectory(prefix="research-v2-offline-") as temporary:
            if not args.execute:
                return await offline(args.recipe, args.question, Path(temporary))
            if args.authorization.stat().st_size > 16384:
                raise AgentError("authorization_file_limit")
            authorization = SyntheticLiveAuthorization.model_validate_json(
                args.authorization.read_bytes()
            )
            # Durable operator-supplied authorization state. No key is read by offline runs.
            state = args.authorization.parent / "research-v2-budgets"
            budget = CampaignBudget(authorization, state, origin="synthetic_live")
            try:
                return await investigate(args.recipe, args.question, budget, allow_network=True)
            finally:
                budget.close()

    try:
        result, _ = asyncio.run(run())
    except (OSError, ValidationError):
        result = {
            "status": "blocked",
            "reason": "invalid_local_authorization_or_configuration",
            "real_qwen": "not_run",
            "production_enabled": False,
        }
    except (AgentError, ValueError) as error:
        result = {
            "status": "blocked",
            "reason": str(error),
            "real_qwen": "not_run",
            "production_enabled": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    if "goal" in result:
        args.output.with_suffix(".md").write_text(markdown(result), encoding="utf-8")
        args.output.with_suffix(".technical.md").write_text(
            technical_markdown(result), encoding="utf-8"
        )
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in ("status", "stop_reason", "model_requests", "tool_attempts", "real_qwen")
            }
        )
    )
    return int(result["status"] not in ("completed",))


if __name__ == "__main__":
    raise SystemExit(main())
