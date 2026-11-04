"""Durable campaign ledger; unknown usage never refunds a reservation or triggers a retry."""

import json
import os
from dataclasses import asdict
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
from .pricing import PRICE
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
        if (
            expiry < today
            or expiry > date.fromisoformat(PRICE.valid_until)
            or self.price_fingerprint != PRICE.fingerprint
        ):
            raise AgentError("stale_authorization_or_price")
        for recipe, fingerprint in self.source_fingerprints.items():
            source = admit(recipe)
            source.validate()
            if source.fingerprint != fingerprint:
                raise AgentError("synthetic_fingerprint_mismatch")


class CampaignBudget:
    """A trusted operator creates this once. New planners share it, never reconstruct it.

    The exclusive campaign journal prevents ordinary process/client restart from restoring
    budgets. An existing campaign cannot resume automatically. OS operators can delete
    state; this is not a multi-tenant billing or authentication service.
    """

    def __init__(
        self,
        authorization: SyntheticLiveAuthorization,
        state_dir: Path,
        *,
        origin: Literal["synthetic_live", "http_mock"],
        today: date | None = None,
    ):
        if origin == "synthetic_live":
            deny_live()  # Before authorization processing or creating any ledger.
        if type(authorization) is not SyntheticLiveAuthorization or origin not in (
            "synthetic_live",
            "http_mock",
        ):
            raise AgentError("invalid_live_authorization")
        self._today = today if origin == "http_mock" else None
        self.authorization = SyntheticLiveAuthorization.model_validate_json(
            authorization.model_dump_json()
        )
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
        self._check_authorization()
        PRICE.check(settings.model, settings.base_url, self.today)
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
        tokens = PRICE.max_input_tokens + a.max_output_tokens
        cost = PRICE.cost(PRICE.max_input_tokens, a.max_output_tokens)
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
                and metric.input_tokens <= PRICE.max_input_tokens
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
        cost = PRICE.cost(
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
            "price": asdict(PRICE),
            "price_fingerprint": PRICE.fingerprint,
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
