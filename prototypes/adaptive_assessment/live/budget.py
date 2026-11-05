"""Durable campaign ledger; unknown usage never refunds a reservation or triggers a retry."""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal

from app.ai.edu.discovery.models import AgentError, Strict
from app.ai.edu.qwen import CallMetric
from app.ai.edu.qwen_config import QwenSettings
from pydantic import Field, field_validator

from ..synthetic import AdmittedSynthetic, admit
from .pricing import reviewed_price
from .safety import deny_live


class SyntheticLiveAuthorization(Strict):
    campaign_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,64}$")
    human_approval_reference: str = Field(pattern=r"^[a-zA-Z0-9:_-]{8,120}$")
    purpose: Literal["synthetic_live"]
    model: Literal["qwen3.8-flash"]
    endpoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_fingerprints: dict[str, str] = Field(min_length=1, max_length=8)
    price_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    valid_until: str
    max_requests: int = Field(ge=1, le=6)
    max_output_tokens: int = Field(ge=1, le=800)
    total_token_limit: int = Field(ge=1, le=10_000_000)
    cost_cap_cny: Decimal = Field(gt=0, le=100)

    @field_validator("source_fingerprints")
    @classmethod
    def registered_sources(cls, value):
        for recipe, fingerprint in value.items():
            if admit(recipe).fingerprint != fingerprint:
                raise ValueError("synthetic_fingerprint_mismatch")
        return value

    def check(self, today: date):
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
        for recipe, fingerprint in self.source_fingerprints.items():
            source = admit(recipe)
            source.validate()
            if source.fingerprint != fingerprint:
                raise AgentError("synthetic_fingerprint_mismatch")


@dataclass(frozen=True)
class OfflineSnapshotPermit:
    """Operator-owned DB proof, usable ONLY by the existing HTTP Mock ledger.

    Not JSON, not a Synthetic Live approval, and never accepted by the network origin.
    The proof rechecks actual current teacher/snapshot access on every budget check.
    """

    source: object = field(repr=False)
    campaign_id: str
    price_fingerprint: str
    max_requests: int = 6
    max_output_tokens: int = 800
    total_token_limit: int = 5_955_648
    cost_cap_cny: Decimal = Decimal("4.78")
    model: str = "qwen3.8-flash"
    endpoint_sha256: str = sha256(b"https://dashscope.aliyuncs.com/compatible-mode/v1").hexdigest()

    @property
    def source_fingerprints(self):
        return {self.source.recipe: self.source.fingerprint}

    def check(self, today):
        from prototypes.teacher_agent_bridge.provider import DatabaseSource

        if type(self.source) is not DatabaseSource:
            raise AgentError("offline_database_proof_required")
        self.source.validate()
        if (
            not 1 <= self.max_requests <= 6
            or self.max_output_tokens != 800
            or not 1 <= self.total_token_limit <= 5_955_648
            or not Decimal(0) < self.cost_cap_cny <= Decimal("4.78")
        ):
            raise AgentError("invalid_offline_budget")
        price = reviewed_price(self.price_fingerprint)
        price.check(self.model, "https://dashscope.aliyuncs.com/compatible-mode/v1", today)

    def model_dump_json(self):
        return json.dumps(
            {
                "origin": "http_mock",
                "campaign_id": self.campaign_id,
                "source_fingerprints": self.source_fingerprints,
                "price_fingerprint": self.price_fingerprint,
                "max_requests": self.max_requests,
                "max_output_tokens": self.max_output_tokens,
                "total_token_limit": self.total_token_limit,
                "cost_cap_cny": str(self.cost_cap_cny),
                "model": self.model,
                "endpoint_sha256": self.endpoint_sha256,
            },
            sort_keys=True,
        )


class CampaignBudget:
    """A trusted operator creates this once. New planners share it, never reconstruct it.

    The exclusive campaign journal prevents ordinary process/client restart from restoring
    budgets. An existing campaign cannot resume automatically. OS operators can delete
    state; this is not a multi-tenant billing or authentication service.
    """

    def __init__(
        self,
        authorization: SyntheticLiveAuthorization | OfflineSnapshotPermit,
        state_dir: Path,
        *,
        origin: Literal["synthetic_live", "http_mock"],
        today: date | None = None,
    ):
        # Preserve the existing DB-only permit rejection and error contract.
        if type(authorization) is OfflineSnapshotPermit and origin != "http_mock":
            raise AgentError("invalid_live_authorization")
        if origin == "synthetic_live":
            deny_live()
        if not (
            type(authorization) is SyntheticLiveAuthorization
            and origin in ("synthetic_live", "http_mock")
            or type(authorization) is OfflineSnapshotPermit
            and origin == "http_mock"
        ):
            raise AgentError("invalid_live_authorization")
        self._today = today if origin == "http_mock" else None
        self.authorization = (
            authorization
            if type(authorization) is OfflineSnapshotPermit
            else SyntheticLiveAuthorization.model_validate_json(authorization.model_dump_json())
        )
        self._bound_price = reviewed_price(self.authorization.price_fingerprint)
        self._authorization_fingerprint = sha256(
            self.authorization.model_dump_json().encode()
        ).hexdigest()
        authorization.check(self.today)
        self._origin = origin
        self._entries = []
        self._claimed = False
        self._blocked = None
        self._closed = False
        state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                state_dir / (authorization.campaign_id + ".jsonl"),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            raise AgentError("campaign_already_consumed") from None
        try:
            self._persist()
        except AgentError:
            self.close()
            raise

    @property
    def price(self):
        return self._bound_price

    @property
    def origin(self):
        return self._origin

    def _check_authorization(self):
        if (
            sha256(self.authorization.model_dump_json().encode()).hexdigest()
            != self._authorization_fingerprint
        ):
            raise AgentError("authorization_changed")
        self.authorization.check(self.today)

    @property
    def today(self):
        return self._today or datetime.now(UTC).date()

    def _persist(self):
        try:
            raw = (json.dumps(self.snapshot(), sort_keys=True) + "\n").encode()
            # FileIO may write short; commit the complete record before any network call.
            offset = 0
            while offset < len(raw):
                wrote = os.write(self._fd, raw[offset:])
                if wrote <= 0:
                    raise OSError
                offset += wrote
            os.fsync(self._fd)
        except OSError:
            self._blocked = "budget_journal_failed"
            raise AgentError("budget_journal_failed") from None

    def close(self):
        if not self._closed:
            os.close(self._fd)
            self._closed = True

    def check(self, settings: QwenSettings, source: AdmittedSynthetic) -> None:
        if self._closed or self._blocked:
            raise AgentError(self._blocked or "budget_closed")
        a = self.authorization
        if type(a) is OfflineSnapshotPermit and source is not a.source:
            raise AgentError("offline_source_identity_mismatch")
        self._check_authorization()
        self.price.check(settings.model, settings.base_url, self.today)
        if (
            settings.model != a.model
            or sha256(settings.base_url.rstrip("/").encode()).hexdigest() != a.endpoint_sha256
            or a.source_fingerprints.get(source.recipe) != source.fingerprint
        ):
            raise AgentError("authorization_scope_mismatch")
        source.validate()

    def claim(self, settings: QwenSettings, source: AdmittedSynthetic) -> None:
        self.check(settings, source)
        if self._claimed:
            raise AgentError("campaign_run_already_claimed")
        self._claimed = True
        self._persist()

    def reserve(self) -> int:
        if self._closed or self._blocked:
            raise AgentError(self._blocked or "budget_closed")
        self._check_authorization()
        a = self.authorization
        if not self._claimed:
            raise AgentError("unbound_campaign")
        if any(e["state"] == "reserved" for e in self._entries):
            raise AgentError("request_already_in_flight")
        if len(self._entries) >= a.max_requests:
            raise AgentError("authorized_request_limit")
        tokens = self.price.max_input_tokens + a.max_output_tokens
        cost = self.price.cost(self.price.max_input_tokens, a.max_output_tokens)
        used_tokens = sum(e["charged_tokens"] for e in self._entries)
        used_cost = sum((Decimal(e["charged_cny_upper_bound"]) for e in self._entries), Decimal(0))
        if used_tokens + tokens > a.total_token_limit:
            raise AgentError("authorized_token_limit")
        if used_cost + cost > a.cost_cap_cny:
            raise AgentError("authorized_cost_limit")
        self._entries.append(
            {
                "request": len(self._entries) + 1,
                "state": "reserved",
                "reserved_tokens": tokens,
                "reserved_cny": str(cost),
                "charged_tokens": tokens,
                "charged_cny_upper_bound": str(cost),
                "actual_usage": None,
                "elapsed_ms": None,
                "http_status": None,
                "transport_status": None,
                "error": None,
            }
        )
        self._persist()
        return len(self._entries)

    def settle(self, ticket: int, metric: CallMetric | None, *, failed: bool):
        self._check_authorization()
        if ticket != len(self._entries) or self._entries[-1]["state"] != "reserved":
            raise AgentError("invalid_budget_ticket")
        entry = self._entries[-1]
        if metric is not None:
            entry["elapsed_ms"] = metric.elapsed_ms
            entry["http_status"] = metric.http_status
            entry["transport_status"] = metric.status
        valid = metric is not None and all(
            type(v) is int and v >= 0
            for v in (metric.input_tokens, metric.output_tokens, metric.total_tokens)
        )
        if valid:
            valid = (
                metric.input_tokens + metric.output_tokens == metric.total_tokens
                and metric.input_tokens <= self.price.max_input_tokens
                and metric.output_tokens <= self.authorization.max_output_tokens
                and (
                    metric.cached_input_tokens is None
                    or (
                        type(metric.cached_input_tokens) is int
                        and 0 <= metric.cached_input_tokens <= metric.input_tokens
                    )
                )
            )
        if not valid:
            entry.update(state="usage_unknown_reservation_held", error="unverifiable_usage")
            self._blocked = "unverifiable_usage"
            self._persist()
            raise AgentError("unverifiable_usage")
        cost = self.price.cost(
            metric.input_tokens, metric.output_tokens, metric.cached_input_tokens or 0
        )
        entry.update(
            state="settled",
            actual_usage={
                "input_tokens": metric.input_tokens,
                "output_tokens": metric.output_tokens,
                "total_tokens": metric.total_tokens,
                "cached_input_tokens": metric.cached_input_tokens,
            },
            charged_tokens=metric.total_tokens,
            charged_cny_upper_bound=str(cost),
            error="model_request_failed" if failed else None,
        )
        if failed:
            self._blocked = "model_request_failed"
        self._persist()

    def snapshot(self) -> dict:
        a = self.authorization
        return {
            "campaign_id": a.campaign_id,
            "authorization_fingerprint": self._authorization_fingerprint,
            "source_fingerprints": dict(a.source_fingerprints),
            "origin": self.origin,
            "billing_basis": "verified_list_price_upper_bound_not_invoice",
            "price": asdict(self.price),
            "price_fingerprint": self.price.fingerprint,
            "max_requests": a.max_requests,
            "total_token_limit": a.total_token_limit,
            "cost_cap_cny": str(a.cost_cap_cny),
            "request_count": len(self._entries),
            "blocked": self._blocked,
            "claimed": self._claimed,
            "charged_tokens": sum(e["charged_tokens"] for e in self._entries),
            "charged_cny_upper_bound": str(
                sum(
                    (Decimal(e["charged_cny_upper_bound"]) for e in self._entries),
                    Decimal(0),
                )
            ),
            "entries": json.loads(json.dumps(self._entries)),
        }
