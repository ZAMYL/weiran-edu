"""One bounded structured suggestion request; no transport, credentials or admission here."""

import asyncio
import json
from collections.abc import Awaitable, Callable

from .material_contract import CapabilityRule, MaterialInput, MaterialPlan, Suggestions
from .material_planning import DEFAULT_RULES, plan_capabilities
from .material_profile import build_profile
from .material_validation import validate_material
from .qwen import _unique_pairs

SuggestionSender = Callable[[dict], Awaitable[dict]]


async def understand_materials(
    inputs: tuple[MaterialInput, ...],
    send: SuggestionSender,
    *,
    rules: tuple[CapabilityRule, ...] = DEFAULT_RULES,
) -> MaterialPlan:
    """`send` may wrap existing post_chat only after admission and budget reservation."""
    profile = build_profile(inputs)
    overview = profile.safe()
    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "Suggest roles, field meanings and cross-file student keys as JSON matching "
                    "the supplied schema. Inputs are structure only. "
                    "Use only supplied reference IDs. "
                    "Cite the table for roles, the column for fields and both endpoints for links. "
                    "Mark uncertainty; omit guesses without a basis. Never confirm mappings."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"overview": overview, "schema": Suggestions.model_json_schema()},
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
    }
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 56_000:
        raise ValueError("material_prompt_limit")
    async with asyncio.timeout(30):
        message = await send(payload)
    raw = (
        message.get("content")
        if isinstance(message, dict) and not message.get("tool_calls")
        else None
    )
    try:
        if not isinstance(raw, str) or len(raw.encode()) > 64_000:
            raise ValueError
        suggestions = Suggestions.model_validate(json.loads(raw, object_pairs_hook=_unique_pairs))
    except (ValueError, RecursionError):
        raise ValueError("invalid_material_suggestion") from None
    assessed = validate_material(profile, suggestions)
    return MaterialPlan(
        assessed, plan_capabilities(profile, assessed, rules), profile.locators, overview
    )
