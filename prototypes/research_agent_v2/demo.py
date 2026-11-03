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

from .runtime import investigate, markdown
from .synthetic import RECIPES


class ResearchMock:
    """Data-driven offline mechanism policy, NOT evidence of Qwen reasoning."""

    def choose(self, view: dict) -> dict:
        goal, facts = view["research_goal"], view["facts"]
        selected = [f["fact_id"] for f in facts if f["qualified"]]

        def finish():
            return {
                "name": "finish",
                "arguments": {
                    "selected_fact_ids": selected[:3],
                    "reason": "completed" if selected else "no_valid_findings",
                },
            }

        if view["reflection"]["state"] != "needs_investigation":
            return finish()
        if view["last_result"] and view["last_result"]["status"] == "rejected":
            return finish()
        summary = next(f for f in facts if "items" in f)
        available = {
            i["item_id"]: i
            for i in summary["items"]
            if i.get("state") == "available"
            and i.get("has_variance")
            and len(i.get("exploration_thresholds", [])) > 1
        }
        if goal["type"] in ("relation", "explanation"):
            return {
                "name": "query_dimension_relation",
                "arguments": dict(zip(("left", "right"), goal["items"], strict=True)),
                "basis_fact_ids": [summary["fact_id"]],
            }
        if goal["type"] == "group":
            items = goal["items"]
            relations = [f for f in facts if f.get("type") == "item_relation"]
            if not relations:
                return {
                    "name": "query_dimension_relation",
                    "arguments": {"left": items[0], "right": items[-1]},
                    "basis_fact_ids": [summary["fact_id"]],
                }
            conditions, outcome = items[:-1], items[-1]
        else:
            issues = [
                i
                for i in summary["items"]
                if any(v > 0 for state, v in i.get("states", {}).items() if state != "valid")
            ]
            if issues and not any(f.get("type") == "item_quality_review" for f in facts):
                return {
                    "name": "inspect_item_quality",
                    "arguments": {"item_id": issues[0]["item_id"]},
                    "basis_fact_ids": [summary["fact_id"]],
                }
            items = list(available)
            if len(items) < 2:
                return finish()
            relations = [f for f in facts if f.get("type") == "item_relation"]
            if not relations:
                return {
                    "name": "query_dimension_relation",
                    "arguments": {"left": items[0], "right": items[1]},
                    "basis_fact_ids": [summary["fact_id"]],
                }
            if len(items) < 3:
                return finish()
            conditions = items[:2] if abs(relations[-1]["metrics"]["rho"] or 0) < 0.6 else items[:1]
            outcome = items[2]
        if any(i not in available for i in conditions):
            return finish()
        return {
            "name": "compare_student_groups",
            "arguments": {
                "conditions": [
                    {
                        "item_id": item,
                        "operator": "<=",
                        "threshold": available[item]["exploration_thresholds"][0],
                    }
                    for item in conditions
                ],
                "outcomes": [outcome],
            },
            "basis_fact_ids": [f["fact_id"] for f in facts[-2:]],
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
    if args.output.exists() or args.output.with_suffix(".md").exists():
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
