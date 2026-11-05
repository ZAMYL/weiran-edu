"""Default offline A/B/C report; --execute additionally requires live approval and reviews."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.ai.edu.discovery.models import AgentError
from pydantic import ValidationError

from prototypes.research_agent_v2.runtime import technical_markdown

from .pilot import PilotApproval, PilotFailure, compare, render
from .preflight import proposal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--review-pr", type=int)
    args = parser.parse_args()
    if args.execute and (args.preflight_only or not args.authorization or not args.review_pr):
        parser.error("execute requires operator authorization and independently reviewed PR")
    paths = [args.output, args.output.with_suffix(".md"), args.output.with_suffix(".technical.md")]
    if any(p.exists() for p in paths):
        parser.error("new_output_paths_required")
    state = args.output.parent / (args.output.stem + "-campaigns")
    result = {"real_qwen": "not_run"}
    exit_code = 0
    try:
        endpoint = args.endpoint or os.environ.get("DASHSCOPE_BASE_URL")
        result["preflight"] = proposal(endpoint)
        if not args.preflight_only:
            approval = None
            if args.execute:
                if args.authorization.stat().st_size > 16384:
                    raise AgentError("authorization_file_limit")
                approval = PilotApproval.model_validate_json(args.authorization.read_bytes())
            result["evaluation"] = asyncio.run(
                compare(state, approval=approval, review_pr=args.review_pr, endpoint=endpoint)
            )
            result["real_qwen"] = result["evaluation"]["actual_qwen"]
            if args.execute:
                result["preflight"]["status"] = "approval_and_reviews_verified"
                result["preflight"]["blockers"] = []
            exit_code = int(
                any(
                    a["run"]["status"] != "completed"
                    or a["score"]["wrong_facts"]
                    or a["score"]["source_errors"]
                    or not a["score"]["stop_claim_matches_coverage"]
                    for a in result["evaluation"]["arms"].values()
                    if "score" in a
                )
            )
    except (AgentError, ValueError, OSError, ValidationError) as error:
        exit_code = 2
        result["status"] = "blocked_or_failed"
        result["error"] = str(error) if isinstance(error, AgentError) else "invalid_local_input"
        if isinstance(error, PilotFailure):
            result["partial_failure"] = error.partial
            result["real_qwen"] = error.partial["actual_qwen"]
            if error.partial.get("cancelled"):
                exit_code = 130
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    if "evaluation" in result:
        args.output.with_suffix(".md").write_text(render(result["evaluation"]), encoding="utf-8")
        sections = []
        for label, arm in result["evaluation"]["arms"].items():
            if "run" in arm:
                technical = technical_markdown(arm["run"]).replace(
                    "Research Agent V2.1 技术执行记录",
                    "Research Agent V2.2 调用记录（复用V2.1运行器）",
                )
                if arm["run"]["origin"] == "synthetic_live":
                    technical = technical.replace(
                        "仅合成HTTP Mock；非真实模型效果。",
                        "Synthetic Live请求记录（含失败）；以request_count及核验usage为准。",
                    )
                sections += ["# " + label, technical]
        args.output.with_suffix(".technical.md").write_text("\n".join(sections), encoding="utf-8")
    print(
        json.dumps({"real_qwen": result["real_qwen"], "exit_code": exit_code}, ensure_ascii=False)
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
