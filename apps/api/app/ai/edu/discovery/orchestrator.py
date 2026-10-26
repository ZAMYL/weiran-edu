"""Legacy closed admission; shared execution does not expand data access."""

from uuid import uuid4

from .models import AgentError, AgentRun, Limits, Planner
from .session import run_session
from .synthetic import build
from .tools import ToolExecutor


async def run_agent(
    case_name: str,
    planner: Planner,
    *,
    limits: Limits | None = None,
    mode: str = "offline",
    synthetic_context: tuple[str, str, str] | None = None,
) -> AgentRun:
    if mode != "offline" or getattr(planner, "offline_only", False) is not True:
        raise AgentError("remote_discovery_disabled")
    limits = limits or Limits()
    builder = build(case_name, context=synthetic_context)
    executor = ToolExecutor(builder, "run-" + uuid4().hex[:16], limits)
    return await run_session(executor, planner, limits=limits)
