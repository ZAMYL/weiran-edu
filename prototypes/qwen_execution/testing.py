"""Trusted Python tests only; no route, configuration flag, Live budget or new executor."""

import httpx
from app.ai.edu.discovery.models import AgentError, Limits

from .task_contract import DatabaseTaskInput, _DatabaseTestTransport, _run_database_profiles


async def run_database_profiles_offline(
    binding: DatabaseTaskInput, *, limits: Limits, transport: httpx.MockTransport
) -> tuple:
    """Exercise the real pipeline with the EXISTING task-owned HTTP Mock budget.

    Normal externalization/source/revocation checks still run. Mock handlers are
    trusted test code, not serialized teacher input; tests must prohibit sockets.
    Deliberately refuses Live budgets so no simulated usage enters a Live journal.
    """
    if type(binding) is not DatabaseTaskInput:
        raise AgentError("trusted_database_task_required")
    capability = _DatabaseTestTransport(transport)
    capability.check(binding.budget)
    result, book, executor = await _run_database_profiles(
        binding, limits=limits, _testing=capability
    )
    if result["origin"] != "http_mock" or result["budget"]["origin"] != "http_mock":
        raise AgentError("database_test_origin_conflict")
    result.update(
        simulation=True,
        execution_mode="offline_http_mock",
        actual_remote_requests=0,
        actual_tokens=None,
        actual_cost_cny=None,
        teacher_api_enabled=False,
    )
    return result, book, executor
