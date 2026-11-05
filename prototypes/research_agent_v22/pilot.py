"""Three-arm evaluation reusing the frozen Research Agent and shared Qwen sender."""

import asyncio
import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

from app.ai.edu.discovery.models import AgentError, Strict
from pydantic import Field

from prototypes.adaptive_assessment.live.budget import CampaignBudget, SyntheticLiveAuthorization
from prototypes.adaptive_assessment.live.demo import mock_authorization
from prototypes.adaptive_assessment.planners import AdaptiveFake
from prototypes.research_agent_v2.demo import mock_transport
from prototypes.research_agent_v2.runtime import investigate

from .preflight import (
    LIMITS,
    PLAN_SHA256,
    PRICE,
    QUESTION,
    RECIPE,
    proposal,
    validate_authorization,
)
from .review import command, verify_reviews
from .scoring import grade

# Report paths cannot choose/reset this per-user, durable Live journal root.
LIVE_LEDGER_ROOT = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "state")))
    / "weiran-edu"
    / "research-v22-live-ledgers"
)


class PilotFailure(AgentError):
    def __init__(self, partial: dict):
        super().__init__("pilot_cancelled" if partial.get("cancelled") else "pilot_failed")
        self.partial = partial


class PilotApproval(Strict):
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    code_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    authorization: SyntheticLiveAuthorization


def mock_budget(state: Path, arm: str):
    p = proposal()
    a = mock_authorization(
        RECIPE,
        campaign_id="v22-" + arm,
        price_fingerprint=PRICE.fingerprint,
        valid_until=PRICE.valid_until,
        total_token_limit=p["total_worst_tokens"],
        cost_cap_cny=p["proposed_cost_cap_cny"],
    )
    return CampaignBudget(a, state, origin="http_mock", today=date.fromisoformat(PRICE.checked_on))


def open_live_budget(approval: PilotApproval) -> CampaignBudget:
    return CampaignBudget(approval.authorization, LIVE_LEDGER_ROOT, origin="synthetic_live")


def transport_state(snapshot: dict) -> str:
    if any(
        e.get("transport_status") is not None or e.get("http_status") is not None
        for e in snapshot["entries"]
    ):
        return "attempted"
    return "request_reserved_transport_unconfirmed" if snapshot["request_count"] else "not_run"


def failed_run(output: dict, budget, campaign_id: str, error: BaseException) -> PilotFailure:
    snapshot = budget.snapshot()
    output["cancelled"] = isinstance(error, asyncio.CancelledError)
    output["actual_qwen"] = transport_state(snapshot)
    output["arms"]["C_qwen"] = {
        "status": "failed_or_cancelled",
        "budget": snapshot,
        "actual_tokens": None,
        "invoice_cost_cny": None,
    }
    failure = LIVE_LEDGER_ROOT / (campaign_id + "-" + output["execution_id"] + "-failure.json")
    try:
        with failure.open("x", encoding="utf-8") as stream:
            json.dump(output, stream, ensure_ascii=False, indent=2)
    except OSError:
        output["failure_journal_error"] = "auxiliary_failure_record_unavailable"
    return PilotFailure(output)


async def arm(budget, *, fixed=False, network=False):
    result, executor = await investigate(
        RECIPE,
        QUESTION,
        budget,
        limits=LIMITS,
        allow_network=network,
        transport=None if network else mock_transport(AdaptiveFake() if fixed else None),
    )
    if network:
        result["real_qwen"] = transport_state(result["budget"])
    scored = grade(result, executor)
    return {
        "measurement_origin": "synthetic_live" if network else "offline_mechanism",
        "run": result,
        "score": scored,
        "actual_tokens": result["actual_tokens"] if network else None,
        "calculated_list_price_upper_bound_cny": (
            result["budget"]["charged_cny_upper_bound"] if network else None
        ),
        "invoice_cost_cny": None,
        "mock_usage_is_not_real_billing": not network,
    }


async def compare(
    state: Path,
    *,
    approval: PilotApproval | None = None,
    review_pr: int | None = None,
    endpoint: str | None = None,
) -> dict:
    review = None
    if approval is not None:
        if type(approval) is not PilotApproval or approval.plan_sha256 != PLAN_SHA256:
            raise AgentError("pilot_approval_mismatch")
        if command(["git", "rev-parse", "HEAD"]).strip() != approval.code_revision:
            raise AgentError("unreviewed_revision")
        validate_authorization(approval.authorization, endpoint or "")
        review = verify_reviews(review_pr or 0)
    output = {
        "execution_id": uuid4().hex,
        "plan_sha256": PLAN_SHA256,
        "arms": {},
        "actual_qwen": "not_run",
    }
    for label, fixed in (("A_fixed", True), ("B_http_mock", False)):
        budget = mock_budget(state / label, label)
        try:
            output["arms"][label] = await arm(budget, fixed=fixed)
        finally:
            budget.close()
    if approval is None:
        output["arms"]["C_qwen"] = {
            "status": "not_run",
            "reason": "separate_human_approval_and_code_review_required",
            "actual_tokens": None,
            "invoice_cost_cny": None,
            "calculated_list_price_upper_bound_cny": None,
        }
    else:
        budget = open_live_budget(approval)
        try:
            output["arms"]["C_qwen"] = await arm(budget, network=True)
            output["actual_qwen"] = output["arms"]["C_qwen"]["run"]["real_qwen"]
        except (Exception, asyncio.CancelledError) as error:
            raise failed_run(output, budget, approval.authorization.campaign_id, error) from None
        finally:
            budget.close()
    baseline = output["arms"]["A_fixed"]["score"]["qualified_semantics"]
    for row in output["arms"].values():
        if "score" in row:
            known = row["score"]["qualified_semantics"]
            row["comparison"] = {
                "added_correct_facts": len(set(known) - set(baseline)),
                "missed_baseline_facts": len(set(baseline) - set(known)),
            }
    output["review"] = review or {"status": "not_verified_for_live"}
    return output


def render(result) -> str:
    lines = ["# V2.2 三组调查对照", "", "固定/HTTP Mock属于离线机制；真实模型状态单列。", ""]
    for label, arm_result in result["arms"].items():
        if "score" not in arm_result:
            lines.append(f"- {label}：{arm_result['status']}，无真实Token或费用记录。")
            continue
        s = arm_result["score"]
        lines.append(
            f"- {label}：目标{s['matched_targets']}/{s['target_denominator']}，"
            f"错误{s['wrong_facts']}、来源错误{s['source_errors']}、"
            f"新增{arm_result['comparison']['added_correct_facts']}、"
            f"遗漏{arm_result['comparison']['missed_baseline_facts']}；"
            f"请求{s['model_requests']}、工具{s['tool_attempts']}，停止{s['stop_reason']}。"
        )
    lines += [
        "",
        "数值由原工具计算，答案仅由独立评分器读取。相关及群体差异不证明教学因果。",
        "费用字段为核验用量对应的目录价上界，实际账单尚需账单核对。",
    ]
    return "\n".join(lines) + "\n"
