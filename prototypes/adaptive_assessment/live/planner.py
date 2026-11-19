"""Native Qwen planning over admitted synthetic aggregates; never a Production entry."""

import json
from dataclasses import asdict
from uuid import uuid4

import httpx
from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.discovery.native import NativeDialogue
from app.ai.edu.discovery.session import _run_session
from app.ai.edu.qwen import post_chat
from app.ai.edu.qwen_config import QwenSettings
from pydantic import SecretStr

from ..agent import AdaptiveExecutor, Decision, wire
from ..planners import bound_schemas
from ..synthetic import admit
from .budget import CampaignBudget
from .safety import deny_live


class QwenAdaptivePlanner(NativeDialogue):
    offline_only = False
    decision_type = Decision

    def __init__(
        self,
        executor: AdaptiveExecutor,
        budget: CampaignBudget,
        *,
        allow_network: bool = False,
        transport: httpx.MockTransport | None = None,
        settings: QwenSettings | None = None,
    ):
        self._admit_execution(budget, transport, allow_network)
        settings = settings or QwenSettings(
            SecretStr("synthetic-mock-placeholder"),
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        budget.claim(settings, executor.source)
        self._executor = executor
        self._budget = budget
        self.settings = settings
        self._transport = transport
        self._http = None
        self.metrics = []
        self.choices = []
        self.schemas = self._tool_schemas(executor)
        self._init_dialogue()

    def _tool_schemas(self, executor):
        return bound_schemas(executor.kernel.catalog(executor.limits.minimum_cell))

    def _admit_execution(self, budget, transport, allow_network):
        if budget.origin != "http_mock":
            deny_live()  # Never load credentials, even with a forged/future budget.
        if type(transport) is not httpx.MockTransport or allow_network:
            raise AgentError("mock_transport_required")

    async def aclose(self):
        if self._http is not None:
            await self._http.aclose()

    def check_decision(self, decision: Decision) -> None:
        if decision.name not in self.schemas:
            raise AgentError("unavailable_tool")
        if any(identifier not in self._executor.facts for identifier in decision.basis_fact_ids):
            raise AgentError("unknown_fact_id")
        arguments = self._executor.parse(decision.name, decision.arguments)
        ids = (
            arguments.selected_fact_ids
            if decision.name == "finish"
            else [arguments.fact_id]
            if decision.name == "inspect_discovery_support"
            else []
        )
        if any(identifier not in self._executor.facts for identifier in ids):
            raise AgentError("unknown_fact_id")
        self.choices.append(
            {
                "round": self._requests,
                "tool": decision.name,
                "arguments": arguments.model_dump(),
                "basis_fact_ids": decision.basis_fact_ids,
            }
        )

    async def decide(self, prompt: str) -> str:
        e = self._executor
        self._budget.check(self.settings, e.source)
        view = json.loads(prompt)
        # A caller cannot pass arbitrary raw data to this planner. Source projections are
        # rebuilt/replayed here, not taken from an uploaded JSON or model assertions.
        expected_keys = {
            "instruction",
            "question",
            "remaining_model_requests",
            "remaining_tool_attempts",
            "facts",
            "last_result",
            "analysis_context",
        }
        if set(view) != expected_keys:
            raise AgentError("unsafe_planner_view")
        for fact in e.facts.values():
            e._verify(fact)
        catalog = e.kernel.catalog(e.limits.minimum_cell)
        facts = [f.view for f in e.facts.values()]
        if view["facts"] != facts or view["analysis_context"] != catalog:
            raise AgentError("unsafe_planner_view")
        last = view["last_result"]
        if last is not None:
            if (
                not isinstance(last, dict)
                or last.get("tool") not in self.schemas
                or last.get("status") not in {"accepted", "rejected"}
            ):
                raise AgentError("unsafe_planner_view")
            # Do not copy arbitrary error strings or fact IDs from the caller.
            last = {"tool": last["tool"], "status": last["status"]}
        safe = {
            "analysis_context": catalog,
            "facts": facts,
            "last_result": last,
            "remaining_model_requests": min(
                6 - self._requests,
                self._budget.authorization.max_requests - len(self._budget.snapshot()["entries"]),
            ),
            "remaining_tool_attempts": view["remaining_tool_attempts"],
        }
        if (
            type(safe["remaining_tool_attempts"]) is not int
            or not 0 <= safe["remaining_tool_attempts"] <= 5
        ):
            raise AgentError("unsafe_planner_view")
        # No caller question, source identity, local card, row, locator or evidence ID.
        return await super().decide(json.dumps(safe, ensure_ascii=False, allow_nan=False))

    async def _post(self, payload: dict) -> dict:
        # Reservation is durable before creating a network client or attempting a request.
        self._budget.check(self.settings, self._executor.source)
        ticket = self._budget.reserve()
        before = len(self.metrics)
        try:
            if self._http is None:
                self._http = httpx.AsyncClient(
                    transport=self._transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=self.settings.timeout_seconds,
                )
            message = await post_chat(
                self.settings,
                self._http,
                self.metrics,
                "adaptive_discovery",
                payload,
                self._budget.authorization.max_output_tokens,
                expected_model=self.settings.model,
            )
        except BaseException:
            metric = self.metrics[-1] if len(self.metrics) > before else None
            try:
                self._budget.settle(ticket, metric, failed=True)
            except AgentError:
                pass  # Keep cancellation/timeout visible; ledger retains the reservation.
            raise
        self._budget.settle(ticket, self.metrics[-1], failed=False)
        return message


async def run_synthetic_live(
    recipe: str,
    budget: CampaignBudget,
    *,
    allow_network: bool = False,
    transport: httpx.MockTransport | None = None,
    settings: QwenSettings | None = None,
    limits: Limits | None = None,
):
    """Existing synthetic runner, restricted to HTTP Mock until a reviewed Live service.

    Neither a caller flag nor a nominal approval reference enables remote execution.
    No teacher API exposes this prototype.
    """
    if allow_network or budget.origin != "http_mock":
        deny_live()
    source = admit(recipe)
    source.validate()
    limits = limits or Limits(model_requests=budget.authorization.max_requests, timeout_seconds=30)
    if limits.model_requests > budget.authorization.max_requests:
        raise AgentError("authorized_request_limit")
    executor = AdaptiveExecutor(source, "run-" + uuid4().hex[:16], limits)
    planner = QwenAdaptivePlanner(
        executor,
        budget,
        allow_network=allow_network,
        transport=transport,
        settings=settings,
    )
    try:
        run = await _run_session(
            executor,
            planner,
            limits=limits,
            decision_type=Decision,
            argument_parser=executor.parse,
            analysis_context=executor.kernel.catalog(limits.minimum_cell),
        )
        return {
            "measurement_origin": "offline_mechanism"
            if budget.origin == "http_mock"
            else "synthetic_live",
            "status": run.status,
            "reason": run.reason,
            "model": planner.settings.model,
            "model_choices": planner.choices,
            "tool_ledger": [asdict(t) for t in run.tools],
            "trace": [asdict(t) for t in run.trace],
            "verified_facts": [f.view for f in run.facts],
            "selected_ids": run.selected_ids,
            "cards": run.cards,
            "model_requests": run.model_requests,
            "tool_attempts": run.tool_attempts,
            "elapsed_seconds": run.compute_seconds,
            "budget": budget.snapshot(),
        }
    finally:
        await planner.aclose()


def render_result(result):
    return json.dumps(wire(result), ensure_ascii=False, indent=2, allow_nan=False)
