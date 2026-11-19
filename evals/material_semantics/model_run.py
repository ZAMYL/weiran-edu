"""Model evaluation seam for an independently admitted, budgeted Qwen sender."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from prototypes.adaptive_assessment.live.pricing import Price

from app.ai.edu.qwen import CallMetric

from .runner import (
    aggregate,
    cases,
    dataset_digest,
    evaluate_case,
    payload_digest,
    reviewed_payloads,
)


@dataclass(frozen=True)
class ModelReply:
    message: dict
    metric: CallMetric


async def evaluate_model(
    selected: list[dict],
    send: Callable[[str, str, dict], Awaitable[ModelReply]],
    *,
    require_admission: Callable[[dict[str, str]], None],
    price: Price,
    base_url: str,
    origin: Literal["approved_qwen", "protocol_only"],
) -> dict:
    """Operator integration hook, not a Live entry point.

    The injected server sender must check the digest against its reviewed source,
    reserve/settle the existing campaign ledger and call post_chat. No network,
    credentials, tokens or locally minted authorization are supplied by this module.
    """
    if origin not in {"approved_qwen", "protocol_only"}:
        raise ValueError("invalid_evaluation_origin")
    price.check(price.model, base_url, datetime.now(UTC).date())
    registered = {case["id"]: case for case in cases()}
    if any(registered.get(case.get("id")) != case for case in selected):
        raise PermissionError("evaluation_fixture_not_registered")
    manifest = reviewed_payloads(selected)
    if not 1 <= len(manifest) == len(selected) <= 6:
        raise ValueError("invalid_evaluation_batch_size")
    require_admission(manifest.copy())
    rows, usage = [], []
    for case in selected:
        case_id = case["id"]
        require_admission({case_id: manifest[case_id]})

        async def invoke(payload, case_id=case_id):
            if payload_digest(payload) != manifest[case_id]:
                raise PermissionError("evaluation_payload_changed")
            reply = await send(case_id, manifest[case_id], payload)
            m = reply.metric
            if m.model != price.model:
                raise ValueError("evaluation_model_mismatch")
            prompt = m.input_tokens
            output = m.output_tokens
            cached = m.cached_input_tokens
            estimate = (
                price.cost(prompt, output, cached or 0)
                if prompt is not None
                and output is not None
                and (cached is None or 0 <= cached <= prompt)
                else None
            )
            usage.append(
                {
                    "id": case_id,
                    "status": m.status,
                    "elapsed_ms": m.elapsed_ms,
                    "prompt_tokens": prompt,
                    "completion_tokens": output,
                    "cached_tokens": cached,
                    "total_tokens": m.total_tokens,
                    "estimated_list_price_cny": str(estimate) if estimate is not None else None,
                    "billed_cost_cny": None,
                }
            )
            return reply.message

        row = await evaluate_case(case, invoke)
        require_admission({case_id: manifest[case_id]})
        if origin == "approved_qwen":
            rows.append(row)
    result = {
        "origin": origin,
        "model": price.model,
        "price_id": price.price_id,
        "dataset_sha256": dataset_digest(),
        "payload_sha256": manifest,
        "metrics": aggregate(rows) if origin == "approved_qwen" else None,
        "cases": rows if origin == "approved_qwen" else None,
        "usage": usage,
    }
    return result
