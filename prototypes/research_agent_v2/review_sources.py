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


def specification(recipe: str) -> dict:
    path = Path(__file__).resolve().parents[2] / "evals/research_v21_review/inputs.json"
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    if len(raw) > 1024 * 1024 or sha256(raw).hexdigest() != INPUT_SHA256_LF:
        raise PrototypeError("review_input_fingerprint_mismatch")
    case = next((c for c in json.loads(raw) if c["case_id"] == RECIPES[recipe]), None)
    if case is None:
        raise PrototypeError("unregistered_review_input")
    return {
        "protocol": "research-synthetic-v2",
        "kind": case["kind"],
        "item_ids": [i["item_id"] for i in case["items"]],
        "rows": case["rows"],
        "minimum": case["scale"]["min"],
        "maximum": case["scale"]["max"],
        "step": case["scale"]["step"],
    }
