"""Shared two-phase engine; offline by default, built-in synthetic live only by explicit opt-in."""

import asyncio
from dataclasses import replace

from .models import (
    EvidenceProvider,
    ExecutionMode,
    InsightError,
    InsightReport,
    Limits,
    LLMClient,
    Outcome,
    Plan,
    Proposal,
    RubricProvider,
    SyntheticInput,
)
from .prompts import composition_prompt, planning_prompt
from .tools import ToolExecutor
from .validation import LIMITATIONS, insufficient_report, parse_json, validate_proposal


async def run_insights(
    question: str,
    source: SyntheticInput,
    client: LLMClient,
    evidence_provider: EvidenceProvider,
    rubric_provider: RubricProvider,
    *,
    limits: Limits | None = None,
    mode: ExecutionMode = ExecutionMode.OFFLINE,
    allow_synthetic_live: bool = False,
) -> Outcome:
    """Fresh execution only; errors never return a seemingly successful report."""
    limits = limits or Limits()
    executor = None
    try:
        if source.access.synthetic_only is not True:
            raise InsightError("synthetic_input_required")
        context = None
        if mode is ExecutionMode.OFFLINE:
            if getattr(client, "offline_only", False) is not True:
                raise InsightError("offline_client_required")
        elif mode is ExecutionMode.SYNTHETIC_LIVE:
            if allow_synthetic_live is not True:
                raise InsightError("synthetic_live_opt_in_required")
            if getattr(client, "offline_only", True) is not False:
                raise InsightError("synthetic_live_client_required")
            from .synthetic import builtin_case, case_context

            case = builtin_case(question, source)
            if case is None:
                raise InsightError("synthetic_scenario_required")
            context = case_context(case)
        else:
            raise InsightError("unsupported_execution_mode")
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise InsightError("invalid_synthetic_question")
        executor = ToolExecutor(
            source.dataset, source.access, evidence_provider, rubric_provider, limits=limits
        )
        prompt = planning_prompt(question, executor, synthetic_context=context)
        if len(prompt.encode("utf-8")) > limits.max_total_tool_bytes:
            raise InsightError("prompt_size_limit")
        plan_raw = await asyncio.wait_for(client.plan(prompt), limits.timeout_seconds)
        plan = parse_json(Plan, plan_raw, limits.max_model_bytes)
        assert isinstance(plan, Plan)
        if len(plan.calls) > limits.max_tool_calls:
            raise InsightError("tool_call_limit")
        for call in plan.calls:
            result = executor.execute(call)
            if result.status != "ok":
                raise InsightError(result.error_code)
        # A nonzero denominator is required; zero matches remain a valid observed ratio.
        usable = any(
            item.value is not None
            and item.denominator > 0
            and item.metric in {"mean", "matched_ratio", "matched_ratio_eligible"}
            for item in executor.facts.values()
        )
        if not usable:
            return Outcome(
                "insufficient_data",
                _mode_report(
                    insufficient_report(
                        executor.facts,
                        has_evidence=bool(executor.evidence_ids - executor.rubric_ids),
                        has_rubric=bool(executor.rubric_ids),
                    ),
                    mode,
                ),
                None,
                tuple(executor.trace),
                tuple(executor.facts.values()),
                execution_mode=mode.value,
            )
        prompt = composition_prompt(executor)
        if len(prompt.encode("utf-8")) > limits.max_total_tool_bytes:
            raise InsightError("prompt_size_limit")
        proposal_raw = await asyncio.wait_for(client.compose(prompt), limits.timeout_seconds)
        proposal = parse_json(Proposal, proposal_raw, limits.max_model_bytes)
        assert isinstance(proposal, Proposal)
        report = validate_proposal(
            proposal,
            executor.facts,
            executor.evidence_ids,
            executor.rubric_ids,
            executor.documents,
        )
        report = _mode_report(report, mode)
        return Outcome(
            "validated",
            report,
            None,
            tuple(executor.trace),
            tuple(executor.facts.values()),
            execution_mode=mode.value,
        )
    except TimeoutError:
        error = "model_timeout"
    except InsightError as exc:
        error = str(exc)
    except Exception:
        error = "workflow_failure"
    return Outcome(
        "error",
        None,
        error,
        tuple(executor.trace) if executor else (),
        tuple(executor.facts.values()) if executor else (),
        execution_mode=mode.value if isinstance(mode, ExecutionMode) else "unsupported",
    )


def _mode_report(report: InsightReport, mode: ExecutionMode) -> InsightReport:
    if mode is ExecutionMode.OFFLINE:
        return report
    return replace(
        report,
        limitations=tuple(
            "当前为内置合成场景的在线执行模式；未开放生产鉴权或真实数据外发。"
            if item == LIMITATIONS["offline_synthetic_not_auth"]
            else item
            for item in report.limitations
        ),
    )
