"""Shared evidence-reference and result helpers; no analysis expectations live here."""

from __future__ import annotations

import json
from hashlib import sha256

FAILURE_STATUSES = frozenset(
    {
        "wrong_answer",
        "timeout",
        "memory_limit",
        "runtime_error",
        "format_error",
        "other_failure",
    }
)
NO_TEST_FAILURES = FAILURE_STATUSES | {"compile_error"}
UNKNOWN_FEATURE = None


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def evidence_ref(
    row: dict, kind: str, *, point: str | None = None, feature: str | None = None
) -> dict:
    suffix = kind
    value = None
    if kind == "verdict":
        value = row["verdict"]
    elif kind == "code":
        value = row["code"]
    elif kind == "test":
        suffix = f"test:{point}"
        test = next(test for test in row["tests"] if test["point"] == point)
        value = json.dumps([point, test["status"]])
    elif kind == "feature":
        suffix = f"feature:{feature}"
        value = feature
    else:
        raise ValueError(f"unknown_evidence_kind:{kind}")
    reference_id = f"{kind}:{row['submission_id']}"
    if point is not None:
        reference_id += f":{point}"
    if feature is not None:
        reference_id += f":{feature}"
    return {
        "reference_id": reference_id,
        "source_id": row["source_id"],
        "source_sha256": digest(row["source_id"]),
        "locator": f"{row['location']}#{suffix}",
        "entry_sha256": digest(f"{row['source_id']}#{row['submission_id']}"),
        "item_id": row["problem"],
        "participant_id": row["participant"],
        "kind": kind,
        "value": value,
        "content_sha256": digest(value or ""),
    }


def public_ref(ref: dict) -> dict:
    """Keep code out of result tables while retaining its content binding."""

    result = dict(ref)
    if ref["kind"] == "code":
        result["value"] = "<redacted>"
    return result


def result_shell(case_id: str, system: str) -> dict:
    return {
        "case_id": case_id,
        "system": system,
        "status": "ok",
        "error": None,
        "statistics": {},
        "phenomena": [],
        "features": {},
        "feature_status": {},
        "candidates": [],
        "feature_cohorts": [],
        "cards": [],
        "overview_verdicts": {},
        "root_assertions": 0,
        "source_verified": None,
    }


def rejection(case_id: str, system: str, error: str) -> dict:
    result = result_shell(case_id, system)
    result.update(status="rejected", error=error)
    return result
