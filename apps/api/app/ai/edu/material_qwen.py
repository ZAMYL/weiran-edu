"""One independently approved Qwen suggestion call for DS-04 material intake."""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal

import httpx
from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.durable_teacher_agent.live_admission import DatabaseLiveApproval, load_live_approval
from pydantic import Field, ValidationError

from .discovery.models import AgentError
from .material_contract import Locator, MaterialInput, MaterialPlan
from .material_profile import build_profile
from .material_semantics import suggestion_payload, understand_materials
from .qwen import CallMetric, post_chat
from .qwen_config import QwenSettings

APPROVAL_ENV = "WEIRAN_EDU_MATERIAL_LIVE_APPROVAL"
PURPOSE = "material_semantics_remote_inference"


class MaterialLiveApproval(DatabaseLiveApproval):
    purpose: Literal["material_semantics_remote_inference"]
    max_requests: Literal[1]
    total_token_limit: int = Field(ge=1, le=992_608)
    cost_cap_cny: Decimal = Field(gt=0, le=1)
    ledger_root_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True, repr=False)
class _MaterialScope:
    fingerprint: str
    require_access: Callable[[], None] = field(repr=False)
    recipe = PURPOSE

    def validate(self) -> None:
        self.require_access()


def material_ledger_fingerprint(root: Path) -> str:
    return sha256(os.path.normcase(str(root.resolve())).encode()).hexdigest()


def _packet_fingerprint(inputs: tuple[MaterialInput, ...], payload: dict) -> str:
    packet = {
        "purpose": PURPOSE,
        "source_versions": [item.source_id for item in inputs],
        "request": payload,
    }
    raw = json.dumps(packet, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return sha256(raw.encode()).hexdigest()


def material_review_fingerprint(
    inputs: tuple[MaterialInput, ...],
    *,
    reviewed_header_labels: frozenset[Locator] = frozenset(),
) -> str:
    """Offline review of the exact source versions and proposed outbound projection."""
    profile = build_profile(inputs, reviewed_header_labels=reviewed_header_labels)
    return _packet_fingerprint(inputs, suggestion_payload(profile))


def _load_approval() -> tuple[MaterialLiveApproval, Path]:
    try:
        approval, root = load_live_approval(
            APPROVAL_ENV, MaterialLiveApproval, today=datetime.now(UTC).date()
        )
    except ValidationError:
        raise AgentError("material_live_approval_invalid") from None
    if approval.ledger_root_sha256 != material_ledger_fingerprint(root):
        raise AgentError("material_live_ledger_mismatch")
    return approval, root.resolve()


class _MaterialCampaign(CampaignBudget):
    def __init__(
        self, approval: MaterialLiveApproval, root: Path, *, transport: httpx.MockTransport | None
    ):
        # A trusted test seam uses a separate mock journal and never claims a live approval.
        self._initialize(
            approval,
            root if transport is None else root / "material-offline-test",
            origin="qwen_live" if transport is None else "http_mock",
        )


async def understand_materials_with_qwen(
    inputs: tuple[MaterialInput, ...],
    *,
    require_access: Callable[[], None],
    reviewed_header_labels: frozenset[Locator] = frozenset(),
) -> MaterialPlan:
    """B-02 supplies immutable source versions and a revocable server-side access check."""
    return await _understand(
        inputs,
        require_access=require_access,
        reviewed_header_labels=reviewed_header_labels,
        transport=None,
    )


async def _understand(
    inputs: tuple[MaterialInput, ...],
    *,
    require_access: Callable[[], None],
    reviewed_header_labels: frozenset[Locator],
    transport: httpx.MockTransport | None,
) -> MaterialPlan:
    if transport is not None and type(transport) is not httpx.MockTransport:
        raise AgentError("material_live_invalid_test_transport")
    require_access()
    approval, root = _load_approval()
    settings = QwenSettings.from_env()
    if (
        settings.model != approval.model
        or sha256(settings.base_url.rstrip("/").encode()).hexdigest() != approval.endpoint_sha256
    ):
        raise AgentError("material_live_model_or_endpoint_mismatch")

    def check_access() -> None:
        require_access()
        if _load_approval() != (approval, root):
            raise AgentError("material_live_approval_changed")

    async def send(payload: dict) -> dict:
        source = _MaterialScope(_packet_fingerprint(inputs, payload), require_access)
        if approval.source_fingerprints != {PURPOSE: source.fingerprint}:
            raise AgentError("material_live_packet_not_approved")
        check_access()
        budget = _MaterialCampaign(approval, root, transport=transport)
        try:
            budget.claim(settings, source)
            ticket = budget.reserve()
            metrics: list[CallMetric] = []
            try:
                check_access()
                async with httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, transport=transport
                ) as client:
                    message = await post_chat(
                        settings,
                        client,
                        metrics,
                        "material_semantics",
                        payload,
                        approval.max_output_tokens,
                        expected_model=approval.model,
                    )
            except BaseException:
                try:
                    budget.settle(ticket, metrics[-1] if metrics else None, failed=True)
                except AgentError:
                    pass  # Unknown usage retains its reservation, including on cancellation.
                raise
            budget.settle(ticket, metrics[-1], failed=False)
            check_access()
            return message
        finally:
            budget.close()

    plan = await understand_materials(
        inputs, send=send, reviewed_header_labels=reviewed_header_labels
    )
    check_access()
    return plan
