"""One focused, synthetic investigation using existing collectors; no live switch."""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evals.discovery_v1.baseline import Baseline
from evals.discovery_v1.cases import cases
from evals.discovery_v1.contracts import candidate_input
from evals.discovery_v1.reference import wire
from prototypes.adaptive_assessment.synthetic import admit

from .collector import collect
from .grading import grade_native
from .reference import frozen_spec
from .runner import (
    ROOT,
    baseline_grade,
    check_admitted_input,
    spec_for_recipe,
    verify_dependencies,
)


async def build_record() -> dict:
    """Oracle stays in the local scorer; only admitted aggregate views reach Mock HTTP."""
    verify_dependencies()
    case = next(c for c in cases() if c.case_id == "group-53")
    spec = frozen_spec(case)
    check_admitted_input(spec, admit("five"))
    baseline = Baseline(ROOT)
    deterministic = baseline_grade(case, baseline.run(candidate_input(case), "A_default"))
    with TemporaryDirectory(prefix="edu-focused-investigation-") as directory:
        collected = await collect("five", Path(directory))
        control = await collect("constant17", Path(directory))
    return {
        "schema_version": "edu-focused-investigation-0.1",
        "product_positioning": "未然 EDU——AI 时代的编程能力与学习成效诊断平台",
        "input_fingerprint": spec.fingerprint,
        "participant_count": len(spec.rows),
        "fixed_deterministic": deterministic,
        "offline_http_mock": {
            "evaluation": grade_native(spec, collected),
            "audit": collected.audit,
        },
        "constant_score_control": {
            "evaluation": grade_native(spec_for_recipe("constant17"), control),
            "audit": control.audit,
        },
        "real_qwen": {
            "status": "not_run",
            "reason": "separate_scenario_model_request_token_cny_authorization_required",
            "model": "qwen3.8-flash",
            "requests": 0,
            "actual_tokens": None,
            "actual_cost_cny": None,
        },
        "paid_api_calls": 0,
        "ai_incremental_value": "not_proven",
        "limitations": [
            "Mock decisions are a deterministic offline policy, not measured Qwen autonomy.",
            "Tool arithmetic and sources are independently checked; causal claims are unsupported.",
            "HTTP usage fixtures and price arithmetic are not actual tokens or supplier invoices.",
            "Registered synthetic input only; teacher authentication is not implemented.",
            "Focused examples do not replace the full frozen denominator or historical omissions.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.suffix.lower() != ".json":
        parser.error("new_JSON_output_required_preserve_previous_runs")
    result = wire(asyncio.run(build_record()))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    for key in ("offline_http_mock", "constant_score_control"):
        row = result[key]
        print(
            json.dumps(
                {
                    "profile": key,
                    "tools": [t["tool"] for t in row["audit"]["tool_ledger"]],
                    "evaluation": row["evaluation"],
                },
                ensure_ascii=False,
            )
        )
    return int(
        any(
            not result[k]["evaluation"]["passed"]
            for k in ("offline_http_mock", "constant_score_control")
        )
    )
