"""Explicit synthetic-only Qwen entry reusing the existing profile session and ledger."""

from collections.abc import Callable
from datetime import date
from hashlib import sha256
from pathlib import Path

import httpx
from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.qwen_config import QwenSettings
from pydantic import Field

from prototypes.adaptive_assessment.live.budget import (
    CampaignBudget,
    SyntheticLiveAuthorization,
)
from prototypes.adaptive_assessment.synthetic import AdmittedSynthetic, admit
from prototypes.capability_profiles.agent import ProfilePlanner, run_profile_session
from prototypes.capability_profiles.engine import build_profiles

ROOT = Path(__file__).resolve().parents[2]


def code_fingerprint() -> str:
    """Bind review to the actual AI/kernel source, including uncommitted modifications."""
    digest = sha256()
    for folder in (
        ROOT / "prototypes",
        ROOT / "apps/api/app",
        ROOT / "docs/canonical_assessment",
    ):
        for path in sorted(folder.rglob("*.py")):
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


class ReviewedApproval(SyntheticLiveAuthorization):
    independent_review_reference: str = Field(min_length=8, max_length=200)
    reviewed_code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check(self, today: date):
        super().check(today)
        if self.reviewed_code_sha256 != code_fingerprint():
            raise AgentError("reviewed_code_changed")


class ReviewedBudget(CampaignBudget):
    """Uses the same exclusive journal, reservations and settlement as HTTP Mock."""

    def __init__(
        self,
        approval: ReviewedApproval,
        state_dir: Path | None = None,
        *,
        authorize: Callable[[], None],
        transport: httpx.MockTransport | None = None,
    ):
        if type(approval) is not ReviewedApproval or not callable(authorize):
            raise AgentError("reviewed_approval_required")
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise AgentError("invalid_execution_transport")
        self.authorize = authorize
        self.transport = transport
        self._initialize(
            approval,
            state_dir,
            origin="qwen_live" if transport is None else "http_mock",
        )

    def _check_authorization(self):
        self.authorize()
        super()._check_authorization()

    def check(self, settings, source):
        if type(source) is not AdmittedSynthetic:
            raise AgentError("remote_teacher_source_not_enabled")
        super().check(settings, source)


class ReviewedProfilePlanner(ProfilePlanner):
    def __init__(self, *args, settings: QwenSettings, **kwargs):
        super().__init__(*args, settings=settings, **kwargs)

    def _admit_execution(self, budget, transport, allow_network):
        if type(budget) is not ReviewedBudget or budget.transport is not transport:
            raise AgentError("reviewed_budget_required")
        if allow_network != (transport is None):
            raise AgentError("execution_origin_mismatch")
        budget._check_authorization()


class ReviewedExecution:
    planner = ReviewedProfilePlanner

    def __init__(self, budget: ReviewedBudget, settings: QwenSettings):
        self.budget, self.settings = budget, settings

    def check(self, source, budget, transport):
        if budget is not self.budget or type(budget) is not ReviewedBudget:
            raise AgentError("reviewed_budget_required")
        if transport is not budget.transport:
            raise AgentError("execution_origin_mismatch")
        budget.check(self.settings, source)


async def run_reviewed_profiles(
    recipe: str,
    budget: ReviewedBudget,
    settings: QwenSettings,
) -> tuple:
    """Operator-only entry. No DB access, teacher upload, HTTP route or implicit approval."""
    source = admit(recipe)
    budget.check(settings, source)
    limits = Limits(
        model_requests=budget.authorization.max_requests,
        tool_attempts=5,
        timeout_seconds=30,
    )
    book = build_profiles(source, minimum_cell=limits.minimum_cell)
    result, book, executor = await run_profile_session(
        source,
        book,
        budget,
        limits=limits,
        transport=budget.transport,
        reviewed_execution=ReviewedExecution(budget, settings),
        retain_partial=False,
    )
    # A response arriving after revocation must never become a public result.
    budget.check(settings, source)
    executor.verify_all()
    for fact in executor.facts.values():
        for metric in fact.item_sources:
            executor.resolve(fact.fact_id, metric.key)
    result["source_replay_verified"] = True
    result["teacher_api_enabled"] = False
    return result, book, executor
