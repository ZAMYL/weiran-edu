"""Reviewed list-price snapshot, bounded to Beijing synchronous non-thinking text calls."""

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from urllib.parse import urlsplit

from app.ai.edu.discovery.models import AgentError


@dataclass(frozen=True)
class Price:
    price_id: str = "qwen3.8-flash-beijing-2026-10-26-v1"
    model: str = "qwen3.8-flash"
    checked_on: str = "2026-10-26"
    valid_until: str = "2026-11-02"
    source: str = "https://help.aliyun.com/zh/model-studio/qwen3-8-flash"
    max_input_tokens: int = 991808
    input_per_million: str = "0.8"
    cached_per_million: str = "0.1"
    output_per_million: str = "2.7"

    @property
    def fingerprint(self):
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def check(self, model: str, base_url: str, today: date):
        host = urlsplit(base_url).hostname or ""
        if (
            model != self.model
            or not (
                host == "dashscope.aliyuncs.com"
                or re.fullmatch(r"[a-zA-Z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com", host)
            )
            or not date.fromisoformat(self.checked_on)
            <= today
            <= date.fromisoformat(self.valid_until)
        ):
            raise AgentError("unverified_model_region_or_stale_price")

    def cost(self, inputs: int, outputs: int, cached: int = 0):
        return (
            (inputs - cached) * Decimal(self.input_per_million)
            + cached * Decimal(self.cached_per_million)
            + outputs * Decimal(self.output_per_million)
        ) / Decimal(1_000_000)


PRICE = Price()


# Newly verified list prices; historical approvals retain their old expiry and fingerprint.
RESEARCH_PRICE_20261104 = Price(
    price_id="qwen3.8-flash-beijing-2026-11-04-v1",
    checked_on="2026-11-04",
    valid_until="2026-11-11",
)

# Official Beijing list price checked 2026-11-12; historical approvals stay expired.
REVIEWED_PRICE_20261112 = Price(
    price_id="qwen3.8-flash-beijing-2026-11-12-v1",
    checked_on="2026-11-12",
    valid_until="2026-11-19",
)

# Official Beijing list price rechecked on 2026-11-19. Use a new identity so an
# older approval cannot acquire a longer validity window.
REVIEWED_PRICE_20261119 = Price(
    price_id="qwen3.8-flash-beijing-2026-11-19-v1",
    checked_on="2026-11-19",
    valid_until="2026-11-26",
)

# Official Beijing list price rechecked 2026-11-25: input 0.8 / output 2.7 / cache hit 0.1 CNY
# per million tokens (help.aliyun.com/zh/model-studio/qwen3-8-flash). New identity keeps older
# approvals expired instead of silently extending them.
REVIEWED_PRICE_20261125 = Price(
    price_id="qwen3.8-flash-beijing-2026-11-25-v1",
    checked_on="2026-11-25",
    valid_until="2026-12-02",
)


# Every reviewed snapshot stays registered: older approvals still resolve, with their old expiry.
_REGISTRY = (
    PRICE,
    RESEARCH_PRICE_20261104,
    REVIEWED_PRICE_20261112,
    REVIEWED_PRICE_20261119,
    REVIEWED_PRICE_20261125,
)


def reviewed_price(fingerprint: str) -> Price:
    for price in _REGISTRY:
        if price.fingerprint == fingerprint:
            return price
    raise AgentError("stale_authorization_or_price")


def current_price(today: date) -> Price:
    """Newest snapshot whose review date is already reached; no hand-edited pointer to bump."""
    eligible = [price for price in _REGISTRY if date.fromisoformat(price.checked_on) <= today]
    return max(eligible, key=lambda price: price.checked_on) if eligible else PRICE
