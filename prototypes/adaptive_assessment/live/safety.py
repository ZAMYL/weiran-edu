"""Fail-closed containment until one code-reviewed, operator-ledger Live service exists."""

from typing import NoReturn

from app.ai.edu.discovery.models import AgentError

LIVE_DISABLED = "synthetic_live_disabled_pending_independent_review"


def deny_live() -> NoReturn:
    # No CLI flag, authorization reference, date or path can enable this gate.
    # Reopening requires a separately reviewed service with exact-code approval and
    # one server-owned campaign ledger independent of request/input/report paths.
    raise AgentError(LIVE_DISABLED)
