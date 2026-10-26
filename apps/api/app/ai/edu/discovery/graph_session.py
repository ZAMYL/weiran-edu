"""One ephemeral run ledger shared by graph nodes; no checkpoint or model-visible rows."""

import asyncio
import json
from copy import copy, deepcopy
from dataclasses import replace
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError

from ..models import InsightError
from ..qwen import _unique_pairs
from .capabilities import CapabilityRegistry, RegisteredCapabilities
from .models import AgentError, AgentRun, Decision, Event, Fact, Limits, Planner, ToolEntry
from .projection import model_discovery, model_investigation
from .synthetic import build
from .tools import ToolExecutor, parse_arguments

FATAL_TOOL_ERRORS = {
    "repeated_tool",
    "duplicate_discovery",
    "source_scope_mismatch",
    "tool_output_limit",
}
VALIDATION_ERRORS = (ValidationError, ValueError, TypeError, RecursionError)


class RunSession:
    def __init__(
        self,
        case_name: str,
        planner: Planner,
        limits: Limits,
        synthetic_context: tuple[str, str, str] | None = None,
        capabilities: CapabilityRegistry | None = None,
    ) -> None:
        self.planner, self.limits = planner, limits
        self.run_id = "run-" + uuid4().hex[:16]
        self.executor = ToolExecutor(
            build(case_name, context=synthetic_context), self.run_id, limits
        )
        self.capabilities = capabilities if capabilities is not None else RegisteredCapabilities()
        self.trace: list[Event] = []
        self.tools: list[ToolEntry] = []
        self.model_requests = self.tool_attempts = 0
        self.started = perf_counter()
        self.turn, self.phase = 0, "plan"
        self.memo: dict[tuple[str, int], str] = {}
        self.plan_lock = asyncio.Lock()
        self.cancelled = False
        self.raw: str | None = None
        self.decision: Decision | None = None
        self.arguments = None
        self.staged: tuple[ToolExecutor, tuple[Fact, ...]] | None = None
        self.tool_error: str | None = None
        self.last_result: dict | None = None
        self.failure: str | None = None
        self.proposed_ids: tuple[str, ...] = ()
        self.output: AgentRun | None = None

    def event(self, phase: str, status: str, tool=None, ids=(), error=None) -> None:
        self.trace.append(
            Event(len(self.trace) + 1, self.model_requests, phase, status, tool, tuple(ids), error)
        )

    def _enter(self, node: str, turn: int) -> str | None:
        if self.cancelled:
            raise asyncio.CancelledError
        previous = self.memo.get((node, turn))
        self.event("graph_node", node + (":reused" if previous is not None else ""))
        if previous is not None:
            return previous
        if turn != self.turn or self.phase != node:
            raise AgentError("invalid_graph_transition")
        return None

    def _leave(self, node: str, turn: int, destination: str) -> str:
        self.memo[node, turn] = destination
        self.phase = destination
        if node == "route" and destination == "plan":
            self.turn += 1
        return destination

    def _fail(self, phase: str, code: str) -> str:
        self.event(phase, "rejected", error=code)
        self.failure = code
        return "finalize"

    def _validation_failure(self, exc: Exception) -> str:
        return self._fail(
            "validation", str(exc) if isinstance(exc, AgentError) else "invalid_model_response"
        )

    async def plan(self, turn: int) -> str:
        # Concurrent/replayed entry shares this lock and memo, never a second model request.
        async with self.plan_lock:
            cached = self._enter("plan", turn)
            if cached is not None:
                return cached
            remaining = self.limits.run_seconds - (perf_counter() - self.started)
            if self.model_requests >= self.limits.model_requests:
                destination = self._fail("planning", "model_budget_exhausted")
            elif remaining <= 0:
                destination = self._fail("planning", "run_timeout")
            else:
                destination = await self._request(remaining)
            return self._leave("plan", turn, destination)

    async def _request(self, remaining: float) -> str:
        view = {
            "instruction": (
                "Choose one typed investigation or finish; select only current verified fact IDs. "
                "No arithmetic, prose, causal claims, identities or evidence requests. "
                "Thresholds are exploratory, not teaching standards."
            ),
            "question": "Explore verifiable aggregate scoring patterns; no finding is acceptable.",
            "remaining_model_requests": self.limits.model_requests - self.model_requests,
            "remaining_tool_attempts": self.limits.tool_attempts - self.tool_attempts,
            "facts": [f.view for f in self.executor.facts.values()],
            "last_result": self.last_result,
        }
        prompt = json.dumps(view, ensure_ascii=False, allow_nan=False)
        if len(prompt.encode()) > self.limits.prompt_bytes:
            return self._fail("planning", "prompt_size_limit")
        self.model_requests += 1
        self.event("planning", "requested")
        try:
            async with asyncio.timeout(min(self.limits.timeout_seconds, remaining)):
                self.raw = await self.planner.decide(prompt)
            return "validate_decision"
        except asyncio.CancelledError:
            self.cancelled = True
            self.event("planning", "cancelled", error="cancelled")
            raise
        except (TimeoutError, InsightError) as exc:
            code = (
                "model_timeout"
                if isinstance(exc, TimeoutError) or str(exc) == "model_timeout"
                else "model_request_failed"
            )
            return self._fail("planning", code)
        except VALIDATION_ERRORS as exc:
            return self._validation_failure(exc)

    def validate_decision(self, turn: int) -> str:
        cached = self._enter("validate_decision", turn)
        if cached is not None:
            return cached
        try:
            if not isinstance(self.raw, str) or len(self.raw.encode()) > 16384:
                raise AgentError("invalid_model_response")
            value = json.loads(
                self.raw,
                object_pairs_hook=_unique_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            self.raw = None
            self.decision = Decision.model_validate(value)
            decision = self.decision
            if len(set(decision.basis_fact_ids)) != len(decision.basis_fact_ids) or any(
                identity not in self.executor.facts for identity in decision.basis_fact_ids
            ):
                raise AgentError("unknown_fact_id")
            self.event("model_selection", "accepted", decision.name, decision.basis_fact_ids)
            self.arguments, self.staged, self.tool_error = None, None, None
            if decision.name == "finish":
                self.arguments = parse_arguments("finish", decision.arguments)
                self.proposed_ids = tuple(self.arguments.selected_fact_ids)
                destination = "finalize"
            else:
                if self.tool_attempts >= self.limits.tool_attempts:
                    raise AgentError("tool_budget_exhausted")
                # As in the baseline, even invalid arguments consume an attempted tool slot.
                self.tool_attempts += 1
                self.event("tool_execution", "attempted", decision.name)
                try:
                    self.arguments = parse_arguments(decision.name, decision.arguments)
                    self.capabilities.authorize(decision.name, self.arguments)
                except AgentError as exc:
                    self.tool_error = str(exc)
                destination = "execute_tool"
        except VALIDATION_ERRORS as exc:
            self.raw = None
            destination = self._validation_failure(exc)
        return self._leave("validate_decision", turn, destination)

    def execute_tool(self, turn: int) -> str:
        cached = self._enter("execute_tool", turn)
        if cached is not None:
            return cached
        if self.tool_error is None:
            # Copy only publication containers. The immutable input and exact executor are reused.
            # Work/seen keys are retained on failure, but facts and disclosures are committed later.
            worker = copy(self.executor)
            worker.facts = {
                identity: replace(fact, view=deepcopy(fact.view))
                for identity, fact in self.executor.facts.items()
            }
            worker.released = list(self.executor.released)
            worker.seen = set(self.executor.seen)
            worker.discoveries = set(self.executor.discoveries)
            try:
                facts = worker.execute(self.decision.name, self.arguments)
                self.staged = worker, facts
            except AgentError as exc:
                self.tool_error = str(exc)
            except VALIDATION_ERRORS as exc:
                destination = self._validation_failure(exc)
                return self._leave("execute_tool", turn, destination)
            finally:
                self.executor.seen = worker.seen
                self.executor.compute_seconds = worker.compute_seconds
        return self._leave("execute_tool", turn, "validate_fact")

    def _check_staged(self, worker: ToolExecutor, facts: tuple[Fact, ...]) -> None:
        before = self.executor
        if not facts or len({f.fact_id for f in facts}) != len(facts):
            raise AgentError("source_validation_failed")
        if list(worker.facts) != [*before.facts, *(f.fact_id for f in facts)]:
            raise AgentError("source_validation_failed")
        if any(worker.facts[identity] != fact for identity, fact in before.facts.items()):
            raise AgentError("source_validation_failed")
        for index, fact in enumerate(facts, len(before.facts) + 1):
            if worker.facts[fact.fact_id] != fact:
                raise AgentError("source_validation_failed")
            if fact.fact_id != f"{self.run_id}-f{index:03}" or fact.tool != self.decision.name:
                raise AgentError("source_scope_mismatch")
            if any(
                not set(source.records) <= set(before.builder.scope.records)
                for source in fact.metric_sources
            ):
                raise AgentError("source_scope_mismatch")
            expected = {
                "fact_id": fact.fact_id,
                "qualified": fact.discovery is not None,
                **model_investigation(fact.tool, fact.view),
            }
            if fact.discovery is not None:
                worker._validate_discovery(fact.discovery)
                if fact.metric_sources != tuple(fact.discovery.sources):
                    raise AgentError("source_validation_failed")
                expected["card"] = model_discovery(fact.discovery)
            if fact.view != expected:
                raise AgentError("unsupported_model_projection")
        # Recheck the staged public sets against this run's committed disclosure history.
        if worker.released[: len(before.released)] != before.released:
            raise AgentError("source_validation_failed")
        if worker.discoveries != before.discoveries | {
            worker._identity(fact.discovery) for fact in facts if fact.discovery is not None
        }:
            raise AgentError("source_validation_failed")
        before._gate(list(worker.released[len(before.released) :]), staged=[])
        size = len(
            json.dumps([f.view for f in facts], ensure_ascii=False, allow_nan=False).encode()
        )
        if (
            worker.output_bytes != before.output_bytes + size
            or size > self.limits.output_bytes
            or worker.output_bytes > self.limits.total_output_bytes
        ):
            raise AgentError("tool_output_limit")

    def validate_fact(self, turn: int) -> str:
        cached = self._enter("validate_fact", turn)
        if cached is not None:
            return cached
        try:
            if self.tool_error is None:
                worker, facts = self.staged
                self._check_staged(worker, facts)
                # Single publication point after the independent graph verification node.
                self.executor = worker
                ids = tuple(f.fact_id for f in facts)
                self.tools.append(
                    ToolEntry(
                        self.tool_attempts,
                        self.decision.name,
                        "accepted",
                        ids,
                        arguments=self.arguments.model_dump(),
                    )
                )
                self.event("tool_execution", "computed", self.decision.name)
                self.event("fact_validation", "accepted", self.decision.name, ids)
                self.last_result = {
                    "tool": self.decision.name,
                    "status": "accepted",
                    "fact_ids": ids,
                }
        except AgentError as exc:
            self.tool_error = str(exc)
        except VALIDATION_ERRORS as exc:
            self.staged = None
            return self._leave("validate_fact", turn, self._validation_failure(exc))
        if self.tool_error is not None:
            code = self.tool_error
            self.tools.append(
                ToolEntry(self.tool_attempts, self.decision.name, "rejected", error=code)
            )
            self.event("fact_validation", "rejected", self.decision.name, error=code)
            self.last_result = {"tool": self.decision.name, "status": "rejected", "error": code}
            if code in FATAL_TOOL_ERRORS:
                self.failure = code
        self.staged = None
        return self._leave("validate_fact", turn, "route")

    def route(self, turn: int) -> str:
        cached = self._enter("route", turn)
        if cached is not None:
            return cached
        if self.failure is not None:
            destination = "finalize"
        elif self.model_requests >= self.limits.model_requests:
            destination = self._fail("planning", "model_budget_exhausted")
        else:
            destination = "plan"
        return self._leave("route", turn, destination)

    def finalize(self, turn: int) -> str:
        cached = self._enter("finalize", turn)
        if cached is not None:
            return cached
        cards, selected, reason, status = (), (), self.failure, "failed"
        if self.failure is None:
            try:
                cards = self.executor.finish(self.arguments)
                selected = tuple(self.arguments.selected_fact_ids)
                reason, status = self.arguments.reason, "completed"
                self.event("final_selection", "accepted", "finish", selected)
            except VALIDATION_ERRORS as exc:
                self._validation_failure(exc)
                reason = self.failure
        self.output = AgentRun(
            self.run_id,
            status,
            reason,
            cards,
            selected,
            tuple(self.executor.facts.values()),
            tuple(self.tools),
            tuple(self.trace),
            self.model_requests,
            self.tool_attempts,
            perf_counter() - self.started,
            proposed_ids=self.proposed_ids,
            tool_compute_seconds=self.executor.compute_seconds,
        )
        return self._leave("finalize", turn, "done")
