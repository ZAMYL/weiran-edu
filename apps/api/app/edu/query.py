"""Bounded JSON decoding preserves threshold lexemes before precision validation."""

import json
from decimal import Decimal, DecimalException

from fastapi import Request
from pydantic import ValidationError

from app.core.errors import APIError

from .analysis_models import GroupQuery


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ValueError("FINITE_JSON_REQUIRED")


async def group_query(request: Request) -> GroupQuery:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "请使用JSON筛选条件")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 64 * 1024:
            raise APIError(413, "QUERY_TOO_LARGE", "筛选请求超过限制")
        raw.extend(chunk)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_float=Decimal,
            parse_constant=_constant,
            object_pairs_hook=_pairs,
        )
        return GroupQuery.model_validate(value)
    except (ValueError, ValidationError, DecimalException, RecursionError):
        raise APIError(422, "VALIDATION_ERROR", "筛选条件不符合要求") from None


def group_schema() -> dict:
    """Inline request models so OpenAPI refs resolve inside the enclosing document."""
    schema = GroupQuery.model_json_schema(mode="validation")
    definitions = schema.pop("$defs", {})

    def expand(value: object) -> object:
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(definitions[value["$ref"].rsplit("/", 1)[-1]])
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(schema)
