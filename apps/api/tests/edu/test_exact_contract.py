"""Transport constraints use real FastAPI responses; storage remains integer-valued."""

import json
import re
from fractions import Fraction

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis.edu.discovery.presentation import exact
from app.core.exact_json import MAX_EXACT_DIGITS, MAX_SAFE_INTEGER, ExactJSONResponse, exact_wire
from app.edu.discovery_models import DiscoveryExact
from app.edu.review_models import TextReviewPreview


@pytest.mark.parametrize(
    "numerator,denominator",
    [
        (0, 1),
        (31, 10),
        (-1, 1_000_000),
        (MAX_SAFE_INTEGER, MAX_SAFE_INTEGER),
        (-MAX_SAFE_INTEGER, MAX_SAFE_INTEGER),
        (MAX_SAFE_INTEGER + 1, 1),
        (-MAX_SAFE_INTEGER - 1, 1),
        (1, MAX_SAFE_INTEGER + 1),
        (10**40 + 3, 10**40 + 7),
        (-(10**40 + 3), 10**40 + 7),
        (10 ** (MAX_EXACT_DIGITS - 1) + 3, 10 ** (MAX_EXACT_DIGITS - 1) + 7),
        (-(10 ** (MAX_EXACT_DIGITS - 1) + 3), 10 ** (MAX_EXACT_DIGITS - 1) + 7),
    ],
)
def test_pydantic_then_actual_http_preserves_bounded_arbitrary_integers(numerator, denominator):
    source = exact(Fraction(numerator, denominator))
    # Preserve the original components even for unreduced synthetic pairs.
    source.update(numerator=numerator, denominator=denominator)
    native = DiscoveryExact(**source)
    before = native.model_dump_json()
    assert type(json.loads(before)["numerator"]) is int
    application = FastAPI()

    @application.get(
        "/synthetic-exact", response_model=DiscoveryExact, response_class=ExactJSONResponse
    )
    def response() -> DiscoveryExact:
        return native

    with TestClient(application) as client:
        response = client.get("/synthetic-exact")
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        schema = schemas["DiscoveryExact"]
    assert response.status_code == 200
    wire = response.json()
    for key, value in (("numerator", numerator), ("denominator", denominator)):
        assert type(wire[key]) is (int if abs(value) <= MAX_SAFE_INTEGER else str)
        assert int(wire[key]) == value
        property_schema = schema["properties"][key]
        if "$ref" in property_schema:
            property_schema = schemas[property_schema["$ref"].split("/")[-1]]
        options = property_schema["anyOf"]
        if isinstance(wire[key], str):
            assert re.fullmatch(options[1]["pattern"], wire[key])
            assert len(wire[key]) <= options[1]["maxLength"]
    assert wire["display"] == source["display"]
    assert native.model_dump_json() == before


@pytest.mark.parametrize("key", ["numerator", "denominator"])
@pytest.mark.parametrize("sign", [-1, 1])
def test_component_digit_budget_fails_before_decimal_conversion(key, sign):
    value = {"numerator": 1, "denominator": 1}
    value[key] = sign * 10**5000
    # Negative denominator is rejected as a value, before any string conversion.
    with pytest.raises(ValueError):
        exact_wire(value)


@pytest.mark.parametrize("value", [True, "1", "01", "-0", "+1", "1e3", " 1", 1.5, None])
def test_internal_component_never_coerces_invalid_values(value):
    with pytest.raises(ValueError, match="INVALID_EXACT_RESPONSE"):
        exact_wire({"numerator": value, "denominator": 1})
    with pytest.raises(ValidationError):
        DiscoveryExact(numerator=value, denominator=1, display="1.0000")


@pytest.mark.parametrize("denominator", [0, -1, True, "1", 1.0, None])
def test_zero_negative_or_noninteger_denominator_is_rejected(denominator):
    with pytest.raises(ValueError, match="INVALID_EXACT_RESPONSE"):
        exact_wire({"numerator": 1, "denominator": denominator})
    with pytest.raises(ValidationError):
        DiscoveryExact(numerator=1, denominator=denominator, display="1.0000")


@pytest.mark.parametrize(
    "display",
    [
        None,
        True,
        1,
        "3.1",
        "03.1000",
        "+3.1000",
        "3.10000",
        "NaN",
        "Infinity",
        "3.1000\n",
        "9.9999",
        "x" * 321,
    ],
)
def test_display_cannot_mask_an_invalid_or_inconsistent_value(display):
    with pytest.raises(ValueError, match="INVALID_EXACT_DISPLAY"):
        exact_wire({"numerator": 31, "denominator": 10, "display": display})


@pytest.mark.parametrize(
    "content",
    [
        {"numerator": 1, "denominator": 0, "display": "1.0000"},
        {"numerator": 1, "denominator": 1, "display": "9.0000"},
        {"numerator": 10**MAX_EXACT_DIGITS, "denominator": 1},
        {"numerator": 1, "denominator": 10**MAX_EXACT_DIGITS},
        {"numerator": 10**1000, "denominator": 1, "display": "1.0000"},
        {"count": MAX_SAFE_INTEGER + 1},
        {"student_denominator": MAX_SAFE_INTEGER + 1},
        {"denominator": -1},
        {"offset": MAX_SAFE_INTEGER + 1},
        {"version": MAX_SAFE_INTEGER + 1},
        {"valid_count": -1},
        {"offset": -1},
        {"version": -1},
    ],
)
def test_invalid_generated_output_is_a_fixed_error_not_an_unsafe_200(client, content):
    @client.app.get("/synthetic-invalid", response_class=ExactJSONResponse)
    def response():
        return content

    actual = client.get("/synthetic-invalid")
    assert actual.status_code == 503
    body = actual.json()
    assert body["error"]["code"] == "DATA_INTEGRITY_ERROR"
    assert body["error"]["request_id"] == actual.headers["X-Request-ID"]
    assert actual.headers["Cache-Control"] == "no-store"
    assert "numerator" not in actual.text and "denominator" not in actual.text


def test_safe_legacy_numbers_counts_nulls_and_signed_noncounts_are_unchanged():
    native = {
        "exact": {"numerator": 31, "denominator": 10},
        "count": 0,
        "version": 1,
        "text_review": {"version": 0},
        "offset": MAX_SAFE_INTEGER,
        "denominator": None,
        "delta": -1,
        "public_display_allowed": False,
    }
    assert exact_wire(native) == native


def test_actual_text_review_initial_zero_version_is_unchanged():
    native = TextReviewPreview(
        batch_id="synthetic-batch",
        snapshot_id="synthetic-snapshot",
        target_kind="comment",
        target_id="synthetic-evidence",
        version=0,
        status="withheld",
        display_text=None,
        preview_text=None,
        preview_truncated=False,
    )
    application = FastAPI()

    @application.get(
        "/synthetic-review", response_model=TextReviewPreview, response_class=ExactJSONResponse
    )
    def response() -> TextReviewPreview:
        return native

    with TestClient(application) as client:
        actual = client.get("/synthetic-review")
    assert actual.status_code == 200 and actual.json()["version"] == 0
    assert actual.json() == native.model_dump(mode="json")


@pytest.mark.parametrize("field", ["numerator", "denominator"])
def test_actual_schema_bounds_canonical_decimal_strings(client, field):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    schema = schemas["DiscoveryExact"]["properties"][field]
    if "$ref" in schema:
        schema = schemas[schema["$ref"].split("/")[-1]]
    text = schema["anyOf"][1]
    pattern = text["pattern"]
    first = str(MAX_SAFE_INTEGER + 1)
    assert re.fullmatch(pattern, first)
    assert re.fullmatch(pattern, "9" * MAX_EXACT_DIGITS)
    for invalid in (
        "0",
        "-0",
        "+" + first,
        "0" + first,
        "1e20",
        "９" * 17,
        "9" * (MAX_EXACT_DIGITS + 1),
    ):
        assert re.fullmatch(pattern, invalid) is None
    assert bool(re.fullmatch(pattern, "-" + first)) is (field == "numerator")
    assert text["maxLength"] == MAX_EXACT_DIGITS + (field == "numerator")
