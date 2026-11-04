"""Read only hash-pinned, reviewer-authored synthetic inputs, never answers."""

import json
from hashlib import sha256
from pathlib import Path

from prototypes.adaptive_assessment.data import PrototypeError

RECIPES = {
    "research-v21-review-correlation": "review_corr_scope_20261103",
    "research-v21-review-missing": "review_missing_support_20261103",
    "research-v21-review-decimal": "review_decimal_interaction_20261103",
}
INPUT_SHA256_LF = "a5fd07c8074961ac8415e7bebc62692821e072e59be11162fc745a095668149f"

SIDE_INPUT_SHA256_LF = {
    "review_corr_scope_20261103": (
        "38195c4561a28d2f33dd064e916509399bdd46e941d3df547a950dfdc4b427cd"
    ),
    "review_missing_support_20261103": (
        "a770e5e79079c3d84c3da58da678c394a461f954fa6d8403c6ff027708959faf"
    ),
    "review_decimal_interaction_20261103": (
        "162df5d4b75b3a530795be1abe86b0fcfb429408cbb7829b5c9f8c3513b1fadd"
    ),
}


def specification(recipe: str) -> dict:
    path = Path(__file__).resolve().parents[2] / "evals/research_v21_review/inputs.json"
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    if len(raw) > 1024 * 1024 or sha256(raw).hexdigest() != INPUT_SHA256_LF:
        raise PrototypeError("review_input_fingerprint_mismatch")
    case = next((c for c in json.loads(raw) if c["case_id"] == RECIPES[recipe]), None)
    if case is None:
        raise PrototypeError("unregistered_review_input")
    side_raw = (
        (path.parent / "inputs" / (RECIPES[recipe] + ".json")).read_bytes().replace(b"\r\n", b"\n")
    )
    if (
        len(side_raw) > 1024 * 1024
        or sha256(side_raw).hexdigest() != SIDE_INPUT_SHA256_LF[RECIPES[recipe]]
    ):
        raise PrototypeError("review_input_fingerprint_mismatch")
    side = json.loads(side_raw)
    if (
        any(side[k] != case[k] for k in ("case_id", "kind", "rows", "scale"))
        or [{k: i[k] for k in ("item_id", "label")} for i in side["items"]] != case["items"]
    ):
        raise PrototypeError("review_input_metadata_mismatch")
    return {
        "protocol": "research-synthetic-v2",
        "kind": case["kind"],
        "measurement_level": side["measurement_level"],
        "item_ids": [i["item_id"] for i in case["items"]],
        "rows": case["rows"],
        "minimum": case["scale"]["min"],
        "maximum": case["scale"]["max"],
        "step": case["scale"]["step"],
    }
