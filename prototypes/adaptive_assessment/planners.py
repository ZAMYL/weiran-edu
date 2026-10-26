"""A transparent offline policy and real native HTTP Mock path; not Qwen quality."""

import json
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from app.ai.edu.discovery.mock_http import MockQwenPlanner
from app.ai.edu.discovery.models import AgentError

from .agent import SCHEMAS, Decision
from .analysis import Kernel
from .synthetic import admit


class AdaptiveFake:
    offline_only = True

    async def decide(self, prompt: str) -> str:
        return json.dumps(self.choose(json.loads(prompt)))

    def choose(self, view: dict) -> dict:
        facts = view["facts"]
        if not facts:
            return {"name": "get_dimension_statistics", "arguments": {}}
        summary = next(f for f in facts if "items" in f)
        catalog = {i["item_id"]: i for i in view["analysis_context"]["items"]}
        selected = [f["fact_id"] for f in facts if f["qualified"]]
        support = [f for f in facts if f.get("qualification") == "qualified"]
        if (
            support
            or view["remaining_tool_attempts"] == 0
            or (view["last_result"] and view["last_result"]["status"] == "rejected")
        ):
            return self.finish(selected, summary)
        groups = [f for f in facts if f.get("type") == "item_group_difference"]
        if groups:
            qualified = [f for f in groups if f["qualified"]]
            return (
                {
                    "name": "inspect_discovery_support",
                    "arguments": {"fact_id": qualified[0]["fact_id"]},
                    "basis_fact_ids": [qualified[0]["fact_id"]],
                }
                if qualified
                else self.finish(selected, summary)
            )
        quality = [f for f in facts if f.get("type") == "item_quality_review"]
        issues = []
        for item in summary["items"]:
            if item.get("state") not in ("available", "no_authorized_numeric_data"):
                continue
            n = item["denominator"]
            affected = sum(count for state, count in item["states"].items() if state != "valid")
            if affected:
                issues.append((FractionRatio(affected, n), item["item_id"]))
        # Finite budget: inspect the largest observed quality issue, never fixture labels.
        if issues and not quality:
            item_id = max(issues)[1]
            return {
                "name": "inspect_item_quality",
                "arguments": {"item_id": item_id},
                "basis_fact_ids": [summary["fact_id"]],
            }
        varying = [
            i
            for i in summary["items"]
            if i.get("has_variance")
            and i.get("state") == "available"
            and catalog[i["item_id"]]["relation_eligible"]
            and len(i.get("exploration_thresholds", [])) >= 2
        ]
        varying.sort(key=lambda i: (-i["valid_count"], i["item_id"]))
        if len(varying) < 2:
            return self.finish(selected, summary)
        left, right = varying[:2]
        relations = [f for f in facts if f.get("type") == "item_relation"]
        if not quality and not relations:
            return {
                "name": "query_dimension_relation",
                "arguments": {"left": left["item_id"], "right": right["item_id"]},
                "basis_fact_ids": [summary["fact_id"]],
            }
        if quality or (relations and abs(relations[0]["metrics"]["rho"] or 0) >= 0.6):
            return {
                "name": "compare_student_groups",
                "arguments": {
                    "conditions": [
                        {
                            "item_id": left["item_id"],
                            "operator": "<=",
                            "threshold": left["exploration_thresholds"][0],
                        }
                    ],
                    "outcomes": [right["item_id"]],
                },
                "basis_fact_ids": [(quality or relations)[0]["fact_id"]],
            }
        return self.finish(selected, summary)

    @staticmethod
    def finish(selected, summary):
        return {
            "name": "finish",
            "arguments": {
                "selected_fact_ids": selected[-3:],
                "reason": "completed"
                if selected
                else "insufficient_data"
                if summary["state"] == "insufficient_data"
                else "no_valid_findings",
            },
        }


def FractionRatio(a, b):
    from fractions import Fraction

    return Fraction(a, b) if b else Fraction(0)


def bound_schemas(catalog: dict) -> dict:
    """Expose actual scheme IDs only; metadata contains no expected findings or score rows."""
    ids = [i["item_id"] for i in catalog["items"]]
    pair_ids = [i["item_id"] for i in catalog["items"] if i["relation_eligible"]]
    filter_ids = [i["item_id"] for i in catalog["items"] if i["filter_eligible"]]
    result = {}
    for name, model in SCHEMAS.items():
        if (
            name == "query_dimension_relation"
            and len(pair_ids) < 2
            or name == "compare_student_groups"
            and len(filter_ids) < 2
        ):
            continue
        raw = model.model_json_schema()

        def visit(node, tool_name=name):
            if isinstance(node, dict):
                for key, prop in node.get("properties", {}).items():
                    if key in ("item_id", "left", "right"):
                        prop["enum"] = (
                            pair_ids
                            if tool_name == "query_dimension_relation"
                            else filter_ids
                            if tool_name == "compare_student_groups"
                            else ids
                        )
                    if key == "item_ids":
                        for variant in prop.get("anyOf", [prop]):
                            if "items" in variant:
                                variant["items"]["enum"] = ids
                    if key == "outcomes":
                        prop["items"]["enum"] = pair_ids
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(raw)

        class Schema:
            def __init__(self, value):
                self.value = value

            def model_json_schema(self):
                return deepcopy(self.value)

        result[name] = Schema(raw)
    return result


class AdaptiveMockPlanner(MockQwenPlanner):
    decision_type = Decision

    def __init__(self, transport, catalog):
        schemas = bound_schemas(catalog)
        super().__init__(transport)
        self.schemas = schemas


def mock_transport(policy=None):
    import httpx

    policy = policy or AdaptiveFake()

    def handler(request):
        body = json.loads(request.content)
        view = json.loads(body["messages"][-1]["content"])
        decision = policy.choose(view)
        count = sum(m["role"] == "assistant" for m in body["messages"])
        args = {**decision["arguments"], "basis_fact_ids": decision.get("basis_fact_ids", [])}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"adaptive-call-{count}",
                                    "type": "function",
                                    "function": {
                                        "name": decision["name"],
                                        "arguments": json.dumps(args),
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


class SyntheticLivePlanner(Protocol):
    offline_only: Literal[False]

    async def decide(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class LiveAuthorization:
    source_fingerprint: str
    max_requests: int
    max_output_tokens: int
    total_token_limit: int
    cost_cap_cny: Decimal

    def validate(self, fingerprint):
        if (
            self.source_fingerprint != fingerprint
            or type(self.max_requests) is not int
            or not 1 <= self.max_requests <= 6
            or type(self.max_output_tokens) is not int
            or not 1 <= self.max_output_tokens <= 800
            or type(self.total_token_limit) is not int
            or self.total_token_limit <= 0
            or not isinstance(self.cost_cap_cny, Decimal)
            or not self.cost_cap_cny.is_finite()
            or self.cost_cap_cny <= 0
        ):
            raise AgentError("invalid_live_authorization")


def prepare_synthetic_live(recipe: str, authorization: LiveAuthorization | None = None) -> dict:
    """Prepare the same native schemas; no credential lookup/client creation/network.

    An actual live client, verified pricing/usage guard and human authorization remain
    future work. Neither this spec nor offline_only flags can enable network execution.
    """
    source = admit(recipe)
    source.validate()
    if authorization:
        authorization.validate(source.fingerprint)
    catalog = Kernel(source.envelope).catalog()
    schemas = bound_schemas(catalog)
    return {
        "status": "prepared_remote_disabled" if authorization else "authorization_required",
        "remote_enabled": False,
        "model": "qwen3.8-flash",
        "tools": [
            {
                "type": "function",
                "function": {"name": name, "parameters": schema.model_json_schema()},
            }
            for name, schema in schemas.items()
        ],
        "source_fingerprint": source.fingerprint,
        "max_requests": authorization.max_requests if authorization else 6,
        "max_output_tokens": authorization.max_output_tokens if authorization else 800,
        "cost_cap_cny": str(authorization.cost_cap_cny) if authorization else None,
        "total_token_limit": authorization.total_token_limit if authorization else None,
        "missing_before_live": [
            "reviewed_synthetic_live_client",
            "explicit_human_request_token_cost_authorization",
            "verified_endpoint_and_model",
            "actual_usage_and_pricing_fail_closed_guard",
        ],
        "production_enabled": False,
    }
