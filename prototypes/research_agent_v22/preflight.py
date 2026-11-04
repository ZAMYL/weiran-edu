"""Closed Synthetic Live pilot: a proposal is never human authorization."""

import json
from dataclasses import asdict
from datetime import date
from decimal import ROUND_CEILING, Decimal
from hashlib import sha256
from urllib.parse import urlsplit

from app.ai.edu.discovery.models import AgentError, Limits

from prototypes.adaptive_assessment.live.budget import SyntheticLiveAuthorization
from prototypes.adaptive_assessment.live.pricing import RESEARCH_PRICE_20261104 as PRICE
from prototypes.adaptive_assessment.synthetic import admit

RECIPE = "research-v21-review-decimal"
QUESTION = "识别值得优先核查的评分异常"
LIMITS = Limits(model_requests=6, tool_attempts=5, timeout_seconds=30)
PLAN = {
    "version": "research-live-pilot-0.1",
    "recipe": RECIPE,
    "source_fingerprint": admit(RECIPE).fingerprint,
    "question": QUESTION,
    "model": PRICE.model,
    "model_requests": 6,
    "tool_attempts": 5,
    "output_tokens_per_request": 800,
    "price_fingerprint": PRICE.fingerprint,
    "request_profile": {"region": "cn-beijing", "enable_thinking": False, "stream": False},
}
PLAN_SHA256 = sha256(json.dumps(PLAN, sort_keys=True).encode()).hexdigest()


def endpoint_fingerprint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or parsed.path.rstrip("/") != "/compatible-mode/v1"
    ):
        raise AgentError("invalid_pilot_endpoint")
    PRICE.check(PRICE.model, endpoint, date.fromisoformat(PRICE.checked_on))
    return sha256(endpoint.rstrip("/").encode()).hexdigest()


def proposal(endpoint: str | None = None, *, today: date | None = None) -> dict:
    today = today or date.today()
    tokens = PRICE.max_input_tokens + PLAN["output_tokens_per_request"]
    cost = PRICE.cost(PRICE.max_input_tokens, PLAN["output_tokens_per_request"])
    blockers = ["human_budget_approval_required", "independent_code_review_required"]
    if not date.fromisoformat(PRICE.checked_on) <= today <= date.fromisoformat(PRICE.valid_until):
        blockers.append("price_reverification_required")
    endpoint_hash = endpoint_fingerprint(endpoint) if endpoint else None
    if endpoint_hash is None:
        blockers.append("endpoint_required")
    return {
        "status": "awaiting_approval",
        "real_qwen": "not_run",
        "plan": PLAN,
        "plan_sha256": PLAN_SHA256,
        "price": asdict(PRICE),
        "price_sha256": PRICE.fingerprint,
        "endpoint_sha256": endpoint_hash,
        "per_request_worst_tokens": tokens,
        "total_worst_tokens": tokens * 6,
        "output_token_cap": 4800,
        "per_request_cost_upper_bound_cny": str(cost),
        "total_cost_upper_bound_cny": str(cost * 6),
        "proposed_cost_cap_cny": str((cost * 6).quantize(Decimal("0.01"), rounding=ROUND_CEILING)),
        "blockers": blockers,
        "actual_tokens": None,
        "actual_cost_cny": None,
        "billing_basis": "list_price_upper_bound_not_invoice",
        "initial_reservation_uses_full_model_max_input": True,
    }


def validate_authorization(a: SyntheticLiveAuthorization, endpoint: str, *, today=None) -> None:
    a.check(today or date.today())
    p = proposal(endpoint, today=today)
    if (
        a.model != PLAN["model"]
        or a.price_fingerprint != PRICE.fingerprint
        or a.endpoint_sha256 != p["endpoint_sha256"]
        or a.source_fingerprints != {RECIPE: PLAN["source_fingerprint"]}
        or a.max_requests != 6
        or a.max_output_tokens != 800
        or a.total_token_limit != p["total_worst_tokens"]
        or a.cost_cap_cny != Decimal(p["proposed_cost_cap_cny"])
    ):
        raise AgentError("pilot_authorization_mismatch")
