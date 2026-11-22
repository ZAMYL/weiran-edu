"""Score structured suggestions separately from DS-04A's permission decisions."""

import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.ai.edu.material_contract import MaterialInput, Suggestions
from app.ai.edu.material_profile import build_profile
from app.ai.edu.material_rules import rule_suggestions
from app.ai.edu.material_semantics import suggestion_payload, understand_materials
from app.ai.edu.models import InsightError
from app.ai.edu.qwen import _unique_pairs

DATA = Path(__file__).with_name("cases.json")


@dataclass(frozen=True)
class Table:
    name: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Document:
    kind: str
    tables: tuple[Table, ...]
    text_lines: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def dataset_digest() -> str:
    return sha256(DATA.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def cases(split="all"):
    bundle = json.loads(DATA.read_bytes())
    assert bundle["version"] == 1
    return [c for c in bundle["cases"] if split in ("all", c["split"])]


def inputs(case: dict) -> tuple[MaterialInput, ...]:
    return tuple(
        MaterialInput(
            Document(
                doc["kind"],
                tuple(
                    Table(
                        f"synthetic local table {i}",
                        tuple(t["header"]),
                        tuple(tuple(row) for row in t["rows"]),
                    )
                    for i, t in enumerate(doc["tables"])
                ),
                warnings=tuple(doc.get("warnings", ())),
            ),
            f"synthetic-source-{index}",
            doc["coverage"],
        )
        for index, doc in enumerate(case["documents"])
    )


def decode(content: str) -> Suggestions:
    if not isinstance(content, str) or len(content.encode()) > 64000:
        raise ValueError("invalid_response")
    return Suggestions.model_validate(json.loads(content, object_pairs_hook=_unique_pairs))


def _link(left: str, right: str, cardinality: str) -> tuple[str, str, str]:
    if cardinality == "one_to_one":
        left, right = sorted((left, right))
    return left, right, cardinality


def _counts(predicted: list[tuple], expected: set[tuple]) -> dict:
    unique = set(predicted)
    return {
        "tp": len(unique & expected),
        "fp": len(predicted) - len(unique & expected),
        "fn": len(expected - unique),
    }


def score(gold: dict, suggestions: Suggestions | None) -> dict:
    expected_roles = {(k, v) for k, v in gold["roles"].items() if v is not None}
    expected_fields = {(k, v) for k, v in gold["fields"].items() if v is not None}
    expected_links = {_link(r["left"], r["right"], r["cardinality"]) for r in gold["links"]}
    roles = [(r.table, r.role) for r in suggestions.roles] if suggestions else []
    fields = [(r.column, r.meaning) for r in suggestions.fields] if suggestions else []
    links = (
        [_link(r.left, r.right, r.cardinality) for r in suggestions.links] if suggestions else []
    )
    known = {(k, v) for k, v in gold["roles"].items() if v is not None}
    by_table = {table: [role for ref, role in roles if ref == table] for table in gold["roles"]}
    unknowns = [
        predictions for table, predictions in by_table.items() if gold["roles"][table] is None
    ]
    unknowns += [
        [meaning for key, meaning in fields if key == column]
        for column, meaning in gold["fields"].items()
        if meaning is None
    ]
    return {
        "roles": _counts(roles, expected_roles),
        "fields": _counts(fields, expected_fields),
        "links": _counts(links, expected_links),
        "role_exact": sum(by_table[ref] == [role] for ref, role in known),
        "role_total": len(known),
        "unknown_total": len(unknowns),
        "field_exact": sum(
            [value for ref, value in fields if ref == expected_ref] == [meaning]
            for expected_ref, meaning in expected_fields
        ),
        "field_total": len(expected_fields),
        "unknown_abstained": sum(not r for r in unknowns),
        "unsubstantiated": (
            len(set(roles) - expected_roles)
            + len(set(fields) - expected_fields)
            + len(set(links) - expected_links)
        ),
    }


def safety(plan, gold: dict) -> dict:
    assessed = plan.assessment
    confirmed = sum(
        v.state == "confirmed"
        for group in (assessed.roles, assessed.fields, assessed.links)
        for v in group.values()
    )
    reviews = []
    for row in gold["links"]:
        key = (row["left"], row["right"], row["cardinality"])
        verdict = assessed.links.get(key)
        reviews.append(verdict.state if verdict else "not_proposed")
    return {
        "without_authority_confirmed": confirmed,
        "without_authority_available": [c.name for c in plan.capabilities if c.available],
        "link_reviews": reviews,
        "field_review": {
            ref: v.state
            if (v := assessed.fields.get((ref, gold["fields"][ref])))
            else "not_proposed"
            for ref in gold.get("field_review", {})
        },
        "states": dict(
            Counter(
                v.state
                for group in (assessed.roles, assessed.fields, assessed.links)
                for v in group.values()
            )
        ),
    }


async def evaluate_case(case: dict, sender) -> dict:
    """Sender receives only the DS-04A payload; reference answers never reach it."""
    gold = case["gold"]
    captured = None
    sent = None
    plan = None
    error = None

    async def record(payload):
        nonlocal captured, sent
        sent = payload_digest(payload)
        response = await sender(payload)
        try:
            if not isinstance(response, dict) or response.get("tool_calls"):
                raise ValueError("invalid_response")
            captured = decode(response.get("content"))
        except (ValueError, RecursionError):
            captured = None
        return response

    try:
        plan = await understand_materials(inputs(case), record)
    except ValueError as exc:
        error = (
            "invalid_reference"
            if str(exc) == "invalid_material_suggestion_reference"
            else "invalid_format"
            if str(exc) == "invalid_material_suggestion"
            else "evaluation_input_or_sender_error"
        )
    except TimeoutError:
        error = "timeout"
    except InsightError:
        error = "model_transport_failure"
    return {
        "id": case["id"],
        "split": case["split"],
        "prompt_sha256": sent,
        "error": error,
        "score": score(gold, captured),
        "safety": safety(plan, gold) if plan else None,
        "errors": diagnostics(gold, captured),
    }


def diagnostics(gold, suggestions):
    if suggestions is None:
        return []
    details = []
    for label, mapping, proposed in (
        ("role", gold["roles"], ((r.table, r.role) for r in suggestions.roles)),
        ("field", gold["fields"], ((r.column, r.meaning) for r in suggestions.fields)),
    ):
        actual = set(proposed)
        for ref, value in sorted(actual):
            if ref not in mapping or value != mapping[ref]:
                reason = (
                    "unknown_reference"
                    if ref not in mapping
                    else "reference_requires_abstention"
                    if mapping[ref] is None
                    else "wrong_semantics"
                )
                details.append({"kind": label, "ref": ref, "reason": reason})
        for ref, value in mapping.items():
            if value is not None and (ref, value) not in actual:
                details.append({"kind": label, "ref": ref, "reason": "gold_not_recovered"})
    expected = {_link(r["left"], r["right"], r["cardinality"]) for r in gold["links"]}
    proposed = {_link(r.left, r.right, r.cardinality) for r in suggestions.links}
    details.extend(
        {"kind": "link", "ref": list(k), "reason": "wrong_or_unproved_link"}
        for k in sorted(proposed - expected)
    )
    details.extend(
        {"kind": "link", "ref": list(k), "reason": "gold_not_recovered"}
        for k in sorted(expected - proposed)
    )
    return details


def aggregate(results: list[dict]) -> dict:
    summary = {}
    for key in ("roles", "fields", "links"):
        counts = Counter()
        for result in results:
            counts.update(result["score"][key])
        tp, fp, fn = (counts[k] for k in ("tp", "fp", "fn"))
        summary[key] = dict(
            tp=tp,
            fp=fp,
            fn=fn,
            precision=tp / (tp + fp) if tp + fp else None,
            recall=tp / (tp + fn) if tp + fn else None,
        )
    for key in ("role", "field"):
        total = sum(r["score"][f"{key}_total"] for r in results)
        exact = sum(r["score"][f"{key}_exact"] for r in results)
        summary[f"{key}_exact_accuracy"] = exact / total if total else None
    for key in ("unknown_total", "unknown_abstained", "unsubstantiated"):
        summary[key] = sum(r["score"][key] for r in results)
    summary["response_errors"] = dict(Counter(r["error"] for r in results if r["error"]))
    return summary


async def rules_sender(payload: dict) -> dict:
    overview = json.loads(payload["messages"][1]["content"])["overview"]
    return {"content": json.dumps(rule_suggestions(overview))}


def blind_spots(case: dict) -> list[str]:
    p = build_profile(inputs(case))
    return [
        ref
        for ref, meaning in case["gold"]["fields"].items()
        if meaning is not None and not p.columns[ref][1].name_tokens
    ]


def payload_digest(payload: dict) -> str:
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reviewed_payloads(selected: list[dict]) -> dict[str, str]:
    return {
        case["id"]: payload_digest(suggestion_payload(build_profile(inputs(case))))
        for case in selected
    }


async def rule_report(split: str) -> dict:
    selected = cases(split)
    runs = []
    for case in selected:
        run = await evaluate_case(case, rules_sender)
        run["masked_gold_fields"] = blind_spots(case)
        runs.append(run)
    return {
        "origin": "deterministic_rules",
        "split": split,
        "dataset_sha256": dataset_digest(),
        "metrics": aggregate(runs),
        "cases": runs,
        "live_model": {"status": "not_run", "quality": None, "calls": None},
    }


def run_rules(split: str) -> dict:
    return asyncio.run(rule_report(split))
