"""Native HTTP fixtures and reproducible audits. Responses are NOT Qwen quality evidence."""

import asyncio
import json
from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from ..planners import AdaptiveFake
from ..synthetic import admit
from .budget import CampaignBudget, SyntheticLiveAuthorization
from .planner import run_synthetic_live
from .pricing import PRICE

MOCK_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MOCK_DATE = date(2026, 10, 26)


def mock_authorization(recipe, **overrides):
    fields = {
        "campaign_id": "mock-campaign-" + recipe,
        "human_approval_reference": "http-mock-not-human-approval",
        "purpose": "synthetic_live",
        "model": PRICE.model,
        "endpoint_sha256": sha256(MOCK_BASE.encode()).hexdigest(),
        "source_fingerprints": {recipe: admit(recipe).fingerprint},
        "price_fingerprint": PRICE.fingerprint,
        "valid_until": PRICE.valid_until,
        "max_requests": 6,
        "max_output_tokens": 800,
        "total_token_limit": 2_000_000,
        "cost_cap_cny": "2",
    }
    fields.update(overrides)
    return SyntheticLiveAuthorization.model_validate_json(json.dumps(fields))


def native_response(decision, index=0, *, usage=True, native_id=None):
    body = {
        "model": PRICE.model,
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": native_id or f"live-mock-{index}",
                            "type": "function",
                            "function": {
                                "name": decision["name"],
                                "arguments": json.dumps(
                                    {
                                        **decision["arguments"],
                                        "basis_fact_ids": decision.get("basis_fact_ids", []),
                                    }
                                ),
                            },
                        }
                    ],
                },
            }
        ],
    }
    if usage:
        body["usage"] = {
            "prompt_tokens": 1000 + index * 100,
            "completion_tokens": 100,
            "total_tokens": 1100 + index * 100,
            "prompt_tokens_details": {"cached_tokens": 100},
        }
    return body


def protocol_mock(policy=None, *, capture=None, mutate=None):
    policy = policy or AdaptiveFake()
    count = 0

    def handler(request):
        nonlocal count
        payload = json.loads(request.content)
        if capture is not None:
            capture.append(payload)
        view = json.loads(payload["messages"][-1]["content"])
        response = native_response(policy.choose(view), count)
        if mutate:
            response = mutate(response, count, view)
        count += 1
        return httpx.Response(200, json=response)

    return httpx.MockTransport(handler)


async def demonstration():
    cases = []
    failures = []
    with TemporaryDirectory(prefix="weiran-mock-only-") as directory:
        for recipe in (
            "five",
            "contest13",
            "contest13_complete",
            "limited",
            "no_evidence",
            "constant17",
            "small",
        ):
            budget = CampaignBudget(
                mock_authorization(recipe),
                Path(directory),
                origin="http_mock",
                today=MOCK_DATE,
            )
            try:
                result = await run_synthetic_live(recipe, budget, transport=protocol_mock())
                cases.append({"recipe": recipe, **result})
            finally:
                budget.close()
        for name in ("missing_usage", "unknown_tool", "cost_cap"):

            def mutate(body, index, view, kind=name):
                if kind == "missing_usage":
                    body.pop("usage", None)
                elif kind == "unknown_tool":
                    body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = (
                        "execute_sql"
                    )
                return body

            overrides = {"campaign_id": "mock-failure-" + name}
            if name == "cost_cap":
                overrides["cost_cap_cny"] = "0.001"
            budget = CampaignBudget(
                mock_authorization("five", **overrides),
                Path(directory),
                origin="http_mock",
                today=MOCK_DATE,
            )
            try:
                result = await run_synthetic_live(
                    "five", budget, transport=protocol_mock(mutate=mutate)
                )
                failures.append({"failure_fixture": name, **result})
            finally:
                budget.close()
    return {
        "failure_cases": failures,
        "measurement_origin": "offline_mechanism",
        "paid_requests": 0,
        "usage_and_cost_are_http_fixtures_not_real_billing": True,
        "cases": cases,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(demonstration()), ensure_ascii=False, indent=2))
