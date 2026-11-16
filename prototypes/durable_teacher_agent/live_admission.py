"""Server-owned admission for real-model database investigations.

Nothing in a request can create this admission: the operator installs one approval
file outside the repository and one ledger root, both read from the environment at
startup. Every other situation fails closed and never falls back to the HTTP mock.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal

from app.ai.edu.discovery.models import AgentError, Strict
from pydantic import Field

from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.pricing import reviewed_price
from prototypes.qwen_execution.runtime import code_fingerprint

APPROVAL_ENV = "WEIRAN_EDU_QWEN_LIVE_APPROVAL"
LEDGER_ROOT_ENV = "WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT"
PURPOSE = "assessment_aggregate_remote_inference"
MAX_APPROVAL_BYTES = 8_192
REPOSITORY = Path(__file__).resolve().parents[2]


class DatabaseLiveApproval(Strict):
    """An independently reviewed permit for one database-sourced Live campaign.

    `source_fingerprints` binds the exact database source digests this approval may
    externalize. It is not the synthetic recipe fingerprint used by #82, so the
    operator cannot reuse a synthetic approval for a teacher batch.
    """

    campaign_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    human_approval_reference: str = Field(pattern=r"^[A-Za-z0-9:_-]{8,120}$")
    purpose: Literal["database_live"]
    model: Literal["qwen3.8-flash"]
    endpoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_fingerprints: dict[str, str] = Field(min_length=1, max_length=16)
    price_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    valid_until: str
    max_requests: int = Field(ge=1, le=6)
    max_output_tokens: int = Field(ge=1, le=800)
    total_token_limit: int = Field(ge=1, le=10_000_000)
    cost_cap_cny: Decimal = Field(gt=0, le=100)
    independent_review_reference: str = Field(min_length=8, max_length=200)
    reviewed_code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check(self, today: date) -> None:
        try:
            expiry = date.fromisoformat(self.valid_until)
        except ValueError:
            raise AgentError("invalid_authorization_expiry") from None
        price = reviewed_price(self.price_fingerprint)
        if (
            expiry < today
            or expiry > date.fromisoformat(price.valid_until)
            or today < date.fromisoformat(price.checked_on)
        ):
            raise AgentError("stale_authorization_or_price")
        if self.reviewed_code_sha256 != code_fingerprint():
            raise AgentError("reviewed_code_changed")


@dataclass(frozen=True)
class ExternalizationScope:
    """Server-generated binding; never built from a request body."""

    teacher_id: str
    task_id: str
    campaign_id: str
    batch_id: str
    snapshot_id: str
    source_sha256: str
    model: str
    endpoint: str
    purpose: str = PURPOSE


@dataclass(frozen=True)
class LiveAdmission:
    approval: DatabaseLiveApproval = field(repr=False)
    ledger_root: Path

    @classmethod
    def load(
        cls, environ: Mapping[str, str] | None = None, *, today: date | None = None
    ) -> LiveAdmission:
        env = os.environ if environ is None else environ
        raw_path = env.get(APPROVAL_ENV)
        if not raw_path:
            raise AgentError("live_admission_not_configured")
        path = Path(raw_path).expanduser()
        if not path.is_absolute() or path.resolve().is_relative_to(REPOSITORY):
            raise AgentError("live_approval_must_stay_outside_repository")
        try:
            payload = path.read_bytes()
        except OSError:
            raise AgentError("live_approval_unreadable") from None
        if len(payload) > MAX_APPROVAL_BYTES:
            raise AgentError("live_approval_too_large")
        approval = DatabaseLiveApproval.model_validate_json(payload)
        approval.check(today or date.today())
        raw_root = env.get(LEDGER_ROOT_ENV)
        if not raw_root:
            raise AgentError("campaign_ledger_root_not_configured")
        root = Path(raw_root).expanduser()
        if not root.is_absolute():
            raise AgentError("campaign_ledger_root_must_be_absolute")
        if root.resolve().is_relative_to(REPOSITORY):
            raise AgentError("campaign_ledger_root_must_stay_outside_repository")
        return cls(approval=approval, ledger_root=root)

    def require(self, scope: ExternalizationScope) -> None:
        """The gate passed to the AI core; it runs before every send and publish."""
        approval = self.approval
        if scope.purpose != PURPOSE:
            raise AgentError("remote_processing_purpose_rejected")
        if approval.model != scope.model:
            raise AgentError("remote_processing_model_mismatch")
        if sha256(scope.endpoint.rstrip("/").encode()).hexdigest() != approval.endpoint_sha256:
            raise AgentError("remote_processing_endpoint_mismatch")
        if approval.source_fingerprints.get(scope.source_sha256) is None:
            raise AgentError("source_not_registered_for_remote")
        approval.check(date.today())

    def budget(self, store, task_id: str, source) -> "DatabaseLiveBudget":
        """One task, one budget, one ledger entry; never a mock budget升级."""
        return DatabaseLiveBudget(self.approval, self.ledger_root, store, task_id, source)


class DatabaseLiveBudget(CampaignBudget):
    """Campaign budget fixed to the server-owned ledger, bound to one durable task."""

    def __init__(
        self,
        approval: DatabaseLiveApproval,
        ledger_root: Path,
        store,
        task_id: str,
        source,
    ):
        self.store, self.task_id = store, task_id
        self._root = ledger_root
        bound = approval.model_copy(
            update={"source_fingerprints": {source.recipe: source.fingerprint}}
        )
        self._initialize(bound, None, origin="qwen_live")

    def _journal_directory(self, state_dir):
        if state_dir is not None:
            raise AgentError("live_ledger_root_is_server_owned")
        return self._root

    def audit_tool(self, phase, executor, entry):
        self.store.audit(self.task_id, phase, executor, entry)
