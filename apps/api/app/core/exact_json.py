"""Lossless rational integers at the validated EDU HTTP response boundary only."""

import json
import math
import re
from fractions import Fraction
from typing import Annotated

from pydantic import Field, WithJsonSchema
from starlette.responses import JSONResponse

from .errors import APIError

MAX_SAFE_INTEGER = 2**53 - 1
MAX_EXACT_DIGITS = 1024
MAX_EXACT_MAGNITUDE = 10**MAX_EXACT_DIGITS
MAX_DISCOVERY_DISPLAY_CHARS = 320
DISCOVERY_DISPLAY_PATTERN = r"^-?(?:0|[1-9][0-9]*)\.[0-9]{4}$"


def _large_decimal_pattern() -> str:
    first = str(MAX_SAFE_INTEGER + 1)
    alternatives = [first, f"[1-9][0-9]{{16,{MAX_EXACT_DIGITS - 1}}}"]
    for index, digit in enumerate(first):
        if digit == "9":
            continue
        tail = len(first) - index - 1
        alternatives.append(
            first[:index] + f"[{int(digit) + 1}-9]" + (f"[0-9]{{{tail}}}" if tail else "")
        )
    return "(?:" + "|".join(alternatives) + ")"


# Only the response schema changes: internal Python values, dumps and stored hashes do not.
type ExactNumerator = Annotated[
    int,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "integer", "minimum": -MAX_SAFE_INTEGER, "maximum": MAX_SAFE_INTEGER},
                {
                    "type": "string",
                    "pattern": "^-?" + _large_decimal_pattern() + "$",
                    "maxLength": MAX_EXACT_DIGITS + 1,
                },
            ]
        },
        mode="serialization",
    ),
]
type ExactDenominator = Annotated[
    int,
    Field(gt=0),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "integer", "minimum": 1, "maximum": MAX_SAFE_INTEGER},
                {
                    "type": "string",
                    "pattern": "^" + _large_decimal_pattern() + "$",
                    "maxLength": MAX_EXACT_DIGITS,
                },
            ]
        },
        mode="serialization",
    ),
]


type DiscoveryDisplay = Annotated[
    str, Field(max_length=MAX_DISCOVERY_DISPLAY_CHARS, pattern=DISCOVERY_DISPLAY_PATTERN)
]


def discovery_display(numerator: int, denominator: int) -> str:
    """Validate the existing kernel's finite binary-float/four-place display policy.

    This is presentation validation only: never use it for facts or ranking arithmetic.
    The original integer pair is neither reduced nor rewritten at this boundary.
    """
    try:
        approximate = float(Fraction(numerator, denominator))
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError("INVALID_EXACT_DISPLAY") from error
    if not math.isfinite(approximate):
        raise ValueError("INVALID_EXACT_DISPLAY")
    return f"{approximate:.4f}"


def exact_wire(value: object, *, field_name: str | None = None) -> object:
    """Project only the two exact-rational shapes in strict, validated response DTOs.

    Safe ordinary integers keep their type. Unsafe non-exact integers fail closed rather
    than disguising counts/versions as strings. Never call this on stored snapshots.
    """
    if isinstance(value, dict):
        if value.keys() in ({"numerator", "denominator"}, {"numerator", "denominator", "display"}):
            numerator, denominator = value["numerator"], value["denominator"]
            if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
                raise ValueError("INVALID_EXACT_RESPONSE")
            if abs(numerator) >= MAX_EXACT_MAGNITUDE or denominator >= MAX_EXACT_MAGNITUDE:
                raise ValueError("EXACT_COMPONENT_TOO_LONG")
            if "display" in value:
                display = value["display"]
                if (
                    not isinstance(display, str)
                    or len(display) > MAX_DISCOVERY_DISPLAY_CHARS
                    or re.fullmatch(DISCOVERY_DISPLAY_PATTERN, display) is None
                    or display != discovery_display(numerator, denominator)
                ):
                    raise ValueError("INVALID_EXACT_DISPLAY")
            return {
                **value,
                "numerator": numerator if abs(numerator) <= MAX_SAFE_INTEGER else str(numerator),
                "denominator": denominator if denominator <= MAX_SAFE_INTEGER else str(denominator),
            }
        return {key: exact_wire(item, field_name=key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [exact_wire(item) for item in value]
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("UNSAFE_JSON_INTEGER")
        if field_name == "limit" and value <= 0:
            raise ValueError("INVALID_JSON_COUNT")
        if (
            field_name is not None
            and (
                field_name.endswith(("_count", "_denominator"))
                or field_name
                in {
                    "count",
                    "total",
                    "offset",
                    "returned",
                    "planned",
                    "evaluated",
                    "passed",
                    "denominator",
                    "version",
                    "expected_version",
                }
            )
            and value < 0
        ):
            raise ValueError("INVALID_JSON_COUNT")
    return value


def exact_json_bytes(value: object) -> bytes:
    return json.dumps(
        exact_wire(value), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


class ExactJSONResponse(JSONResponse):
    """Runs after FastAPI response validation; explicit file Responses bypass it."""

    def render(self, content: object) -> bytes:
        try:
            return exact_json_bytes(content)
        except (ValueError, OverflowError) as error:
            raise APIError(
                503, "DATA_INTEGRITY_ERROR", "测评数据完整性校验失败，无法生成结果"
            ) from error
