"""Opt-in StateGraph orchestration; private context, no persistence or cloud callbacks."""

import asyncio
from dataclasses import dataclass, field
from typing import TypedDict

from langchain_core.tracers.context import tracing_v2_callback_var
from langgraph.errors import NodeCancelledError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from .capabilities import CapabilityRegistry
from .graph_session import RunSession
from .models import AgentError, AgentRun, Limits, Planner


class GraphState(TypedDict):
    turn: int
    destination: str


@dataclass(frozen=True, repr=False)
class RunContext:
    # Not a graph channel. Even streaming graph state cannot expose this private input holder.
    session: RunSession = field(repr=False)


async def plan(state: GraphState, runtime: Runtime[RunContext]) -> GraphState:
    return {**state, "destination": await runtime.context.session.plan(state["turn"])}


async def validate_decision(state: GraphState, runtime: Runtime[RunContext]) -> GraphState:
    return {**state, "destination": runtime.context.session.validate_decision(state["turn"])}


async def execute_tool(state: GraphState, runtime: Runtime[RunContext]) -> GraphState:
    return {**state, "destination": runtime.context.session.execute_tool(state["turn"])}


async def validate_fact(state: GraphState, runtime: Runtime[RunContext]) -> GraphState:
    return {**state, "destination": runtime.context.session.validate_fact(state["turn"])}


async def route(state: GraphState, runtime: Runtime[RunContext]) -> GraphState:
    destination = runtime.context.session.route(state["turn"])
    return {"turn": state["turn"] + (destination == "plan"), "destination": destination}


async def finalize(state: GraphState, runtime: Runtime[RunContext]) -> GraphState:
    return {**state, "destination": runtime.context.session.finalize(state["turn"])}


def _destination(state: GraphState) -> str:
    return state["destination"]


def _build_graph() -> CompiledStateGraph:
    graph = StateGraph(GraphState, context_schema=RunContext)
    for name, function in (
        ("plan", plan),
        ("validate_decision", validate_decision),
        ("execute_tool", execute_tool),
        ("validate_fact", validate_fact),
        ("route", route),
        ("finalize", finalize),
    ):
        graph.add_node(name, function, retry_policy=None, cache_policy=None)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan", _destination, {"validate_decision": "validate_decision", "finalize": "finalize"}
    )
    graph.add_conditional_edges(
        "validate_decision", _destination, {"execute_tool": "execute_tool", "finalize": "finalize"}
    )
    graph.add_conditional_edges(
        "execute_tool", _destination, {"validate_fact": "validate_fact", "finalize": "finalize"}
    )
    graph.add_edge("validate_fact", "route")
    graph.add_conditional_edges("route", _destination, {"plan": "plan", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=None, store=None, debug=False)


async def run_agent_langgraph(
    case_name: str,
    planner: Planner,
    *,
    limits: Limits | None = None,
    mode: str = "offline",
    synthetic_context: tuple[str, str, str] | None = None,
    capabilities: CapabilityRegistry | None = None,
) -> AgentRun:
    """Same closed synthetic inputs and output contract as run_agent; never the default."""
    if mode != "offline" or getattr(planner, "offline_only", False) is not True:
        raise AgentError("remote_discovery_disabled")
    session = RunSession(case_name, planner, limits or Limits(), synthetic_context, capabilities)
    try:
        # Override environment tracing AND inherited LangChain callbacks. Do not expose config,
        # checkpointers, interrupt/resume, stores or arbitrary callbacks through this entry point.
        # Pinned langchain-core also has a separate active-tracer ContextVar, which takes
        # precedence over LangSmith's enabled=False. Clear it locally and restore the caller.
        token = tracing_v2_callback_var.set(None)
        try:
            with tracing_context(enabled=False):
                await _build_graph().ainvoke(
                    {"turn": 0, "destination": "plan"},
                    context=RunContext(session),
                    config={"recursion_limit": 64, "callbacks": []},
                )
        finally:
            tracing_v2_callback_var.reset(token)
    except (asyncio.CancelledError, NodeCancelledError):
        if not session.cancelled:
            session.event("planning", "cancelled", error="cancelled")
        session.cancelled = True
        session.staged = None
        # LangGraph wraps a node-raised cancellation; preserve the legacy asyncio contract.
        raise asyncio.CancelledError from None
    if session.output is None:
        raise AgentError("graph_did_not_finalize")
    return session.output
