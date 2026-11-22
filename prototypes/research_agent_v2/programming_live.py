"""Operator-only C-01 Live execution, with no FastAPI registration."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal

import httpx
from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.qwen_config import QwenSettings
from app.core.errors import APIError
from pydantic import Field, ValidationError

from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.pricing import reviewed_price
from prototypes.durable_teacher_agent.live_admission import DatabaseLiveApproval, LiveAdmission

from .database_evidence import AssessmentEvidenceProvider
from .programming import (
    INSPECT,
    ProgrammingExecutor,
    ProgrammingPlanner,
    _run_programming,
)
from .programming_evidence import EvidenceScope, EvidenceSnapshot, ProgrammingSource
from .programming_live_fixture import synthetic_zip

APPROVAL_ENV = "WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL"
PURPOSE = "programming_synthetic_aggregate_remote_inference"
ZIP_SHA256 = sha256(synthetic_zip()).hexdigest()


class ProgrammingLiveApproval(DatabaseLiveApproval):
    purpose: Literal["programming_synthetic_aggregate_remote_inference"]
    teacher_id: str = Field(min_length=1, max_length=80)
    task_id: str = Field(min_length=1, max_length=80)
    zip_sha256: Literal[ZIP_SHA256]
    ledger_root_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_requests: int = Field(ge=3, le=6)
    total_token_limit: int = Field(ge=1, le=5_955_648)
    cost_cap_cny: Decimal = Field(gt=0, le=5)
    independent_review_reference: str = Field(
        pattern=r"^https://github\.com/ZAMYL/weiran-edu/pull/\d+#(?:issuecomment-|pullrequestreview-)\d+$"
    )


class ProgrammingLiveAdmission(LiveAdmission):
    approval_environment = APPROVAL_ENV
    approval_type = ProgrammingLiveApproval

    @classmethod
    def load(cls, environ=None, *, today=None):
        try:
            return super().load(environ, today=today)
        except ValidationError:
            raise AgentError("programming_live_approval_invalid") from None

    def require_programming(self, scope: EvidenceScope, settings: QwenSettings) -> None:
        current = type(self).load()
        if current != self or type(self.approval) is not ProgrammingLiveApproval:
            raise AgentError("programming_live_approval_changed")
        approval = self.approval
        if (
            scope.actor != approval.teacher_id
            or scope.task != approval.task_id
            or approval.source_fingerprints != {scope.batch: scope.snapshot}
            or approval.ledger_root_sha256 != ledger_fingerprint(self.ledger_root)
        ):
            raise AgentError("programming_live_scope_mismatch")
        if (
            settings.model != approval.model
            or sha256(settings.base_url.rstrip("/").encode()).hexdigest()
            != approval.endpoint_sha256
        ):
            raise AgentError("programming_live_model_or_endpoint_mismatch")
        reviewed_price(approval.price_fingerprint).check(
            settings.model, settings.base_url, date.today()
        )


def ledger_fingerprint(root: Path) -> str:
    return sha256(os.path.normcase(str(root.resolve())).encode()).hexdigest()


@dataclass(frozen=True, repr=False)
class ProgrammingLiveInput:
    """Trusted backend input. Guard must raise when the current task may no longer run."""

    provider: AssessmentEvidenceProvider
    admission: ProgrammingLiveAdmission
    settings: QwenSettings = field(repr=False)
    guard: Callable[[], None] = field(repr=False)

    def authorize(self) -> None:
        try:
            self.guard()
        except APIError as error:
            code = (
                "programming_live_authorization_revoked"
                if error.status_code in (401, 403, 404)
                else "programming_live_authorization_unavailable"
            )
            raise AgentError(code) from None
        self.admission.require_programming(self.provider.scope, self.settings)

    def source(self) -> ProgrammingSource:
        if type(self.provider) is not AssessmentEvidenceProvider:
            raise AgentError("programming_live_assessment_provider_required")
        provider = _LiveEvidenceProvider(
            self.provider.reader, self.provider.principal, self.provider.scope, self.authorize
        )
        return ProgrammingSource(provider.scope, provider)


@dataclass(frozen=True, repr=False)
class _LiveEvidenceProvider(AssessmentEvidenceProvider):
    authorize: Callable[[], None]

    def _overview(self, scope: EvidenceScope):
        self.authorize()
        view = super()._overview(scope)
        if [asset.sha256 for asset in view.assets] != [ZIP_SHA256]:
            raise AgentError("programming_live_source_not_registered")
        return view

    def capture(self, scope: EvidenceScope) -> EvidenceSnapshot:
        snapshot = super().capture(scope)
        self.authorize()
        return snapshot


def _digest(data: object) -> str:
    raw = json.dumps(
        data, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    return sha256(raw.encode()).hexdigest()


class _ProgrammingCampaign(CampaignBudget):
    def __init__(self, binding: ProgrammingLiveInput, *, transport: httpx.MockTransport | None):
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise AgentError("programming_live_test_transport_required")
        self.binding = binding
        self.transport = transport
        self.attempts = 0
        self.receipt = None
        mock_directory = binding.admission.ledger_root / "programming-offline-test"
        self._initialize(
            binding.admission.approval,
            mock_directory if transport is not None else None,
            origin="qwen_live" if transport is None else "http_mock",
        )

    def _journal_directory(self, state_dir):
        if self.transport is None:
            return self.binding.admission.ledger_root
        return super()._journal_directory(state_dir)

    def _check_authorization(self):
        self.binding.authorize()
        super()._check_authorization()

    def audit_tool(self, phase, executor, _entry):
        if phase == "attempt":
            self.binding.authorize()
            count = len(executor.attempt_ledger)
            if not self.attempts < count <= 5:
                raise AgentError("tool_budget_exhausted")
            self.attempts = count
            self._persist()

    def snapshot(self) -> dict:
        return {
            **super().snapshot(),
            "tool_attempts": self.attempts,
            "receipt": self.receipt,
        }


class _LiveProgrammingPlanner(ProgrammingPlanner):
    def _admit_execution(self, budget, transport, allow_network):
        if (
            type(budget) is not _ProgrammingCampaign
            or transport is not budget.transport
            or allow_network != (budget.origin == "qwen_live")
        ):
            raise AgentError("programming_live_budget_required")
        budget._check_authorization()

    async def _post(self, payload: dict) -> dict:
        message = await super()._post(payload)
        self._budget.check(self.settings, self._executor.source)
        return message


def _limits(binding: ProgrammingLiveInput) -> Limits:
    return Limits(model_requests=binding.admission.approval.max_requests, timeout_seconds=30)


def _verified_result(executor: ProgrammingExecutor, reason: str, selected: list[str]) -> dict:
    args = executor.parse("finish", {"reason": reason, "selected_fact_ids": selected})
    # finish verifies the immutable ledger, including each metric's Witness binding.
    cards = executor.finish(args)
    executor.source.provider.require_details(executor.source.scope)
    facts = []
    for fact in executor.facts.values():
        sources = []
        for metric in fact.item_sources:
            witnesses = [executor.kernel.witnesses[ref] for ref in metric.references]
            sources.append(
                {
                    "metric_key": metric.key,
                    "witness_count": len(witnesses),
                    "witnesses_sha256": _digest([asdict(w) for w in witnesses]),
                }
            )
        facts.append({"fact_id": fact.fact_id, "values": fact.view, "witnesses": sources})
    executor.source.validate()
    return {
        "findings": [
            {
                "fact_id": identifier,
                "direction": card["direction"],
                "features": card["features"],
                "limitations": card["limitations"],
                "next_check": card["next_check"],
            }
            for identifier, card in zip(selected, cards, strict=True)
        ],
        "facts": facts,
        "source_replay_verified": True,
    }


def _trace_steps(choices: list[dict], entries: list[dict], facts: list[dict]) -> list[dict]:
    # DS-06B uses stable, already-disclosed roles instead of random per-run Fact IDs.
    roles = {
        fact["fact_id"]: "overview" if index == 0 else f"inspect:{index}"
        for index, fact in enumerate(facts)
    }
    steps = []
    for choice in choices:
        entry = entries[choice["round"] - 1]
        usage = entry["actual_usage"]
        tool = choice["tool"]
        args = choice["arguments"]
        steps.append(
            {
                "decision": {
                    "name": tool,
                    "arguments": (
                        {"direction": args["direction"]}
                        if tool == INSPECT
                        else {
                            "reason": args["reason"],
                            "selected_roles": [
                                roles[identifier] for identifier in args["selected_fact_ids"]
                            ],
                        }
                        if tool == "finish"
                        else {}
                    ),
                    "basis_fact_ids": [
                        roles[identifier] for identifier in choice["basis_fact_ids"]
                    ],
                },
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "latency_ms": entry["elapsed_ms"],
            }
        )
    return steps


async def run_programming_live(binding: ProgrammingLiveInput) -> dict:
    """The actual Beijing HTTP entry. Python code supplies the reviewed server binding."""
    return await _run(binding, transport=None)


async def _run(binding: ProgrammingLiveInput, *, transport: httpx.MockTransport | None) -> dict:
    source = binding.source()
    binding.admission.require_programming(source.scope, binding.settings)
    campaign = _ProgrammingCampaign(binding, transport=transport)
    planner = None
    started = perf_counter()

    def make_planner(executor, budget):
        nonlocal planner
        planner = _LiveProgrammingPlanner(
            executor,
            budget,
            transport=transport,
            settings=binding.settings,
            allow_network=transport is None,
        )
        return planner

    try:
        try:
            run, executor = await _run_programming(
                source,
                source.material.problems,
                campaign,
                limits=_limits(binding),
                planner_factory=make_planner,
            )
            reason = run.reason
        except AgentError as error:
            if planner is None:
                raise
            # The final executor audit may reject a run after an HTTP response arrived.
            run, executor, reason = None, planner._executor, str(error)
        snapshot = campaign.snapshot()
        receipt = {
            "task_id": source.scope.task,
            "snapshot_id": source.scope.snapshot,
            "execution_origin": campaign.origin,
            "state": run.status if run is not None else "failed",
            "reason": reason,
            "run_id": executor.run_id,
            "model_requests": snapshot["request_count"],
            "tool_attempts": snapshot["tool_attempts"],
            "elapsed_seconds": perf_counter() - started,
            "model_choices": planner.choices,
            "tool_ledger": (
                [asdict(tool) for tool in run.tools] if run is not None else executor.attempt_ledger
            ),
            "http_observations": [
                {
                    "http_status": metric.http_status,
                    "status": metric.status,
                    "elapsed_ms": metric.elapsed_ms,
                    # post_chat validates the exact response model before marking it received.
                    "verified_response_model": (
                        metric.model if metric.status == "response_received" else None
                    ),
                }
                for metric in planner.metrics
            ],
        }
        if run is not None and run.status == "completed":
            if (
                snapshot["request_count"] != run.model_requests
                or snapshot["tool_attempts"] != run.tool_attempts
                or any(
                    entry["transport_status"] != "response_received"
                    for entry in snapshot["entries"]
                )
            ):
                raise AgentError("programming_live_execution_accounting_mismatch")
            try:
                result = _verified_result(executor, run.reason, list(run.selected_ids))
                campaign.check(binding.settings, source)
            except (AgentError, APIError) as error:
                receipt.update(
                    state="failed", reason=error.code if isinstance(error, APIError) else str(error)
                )
            else:
                receipt["result"] = result
                receipt["result_sha"] = _digest(result)
                receipt["ds06b"] = {
                    "origin": "approved_qwen" if transport is None else "http_mock",
                    "model_origin_verified": False,
                    "problem": "P1",
                    "cost": float(snapshot["charged_cny_upper_bound"]),
                    "steps": _trace_steps(planner.choices, snapshot["entries"], result["facts"]),
                }
        campaign.receipt = receipt
        campaign._persist()
        return _report(campaign.snapshot(), binding.settings)
    finally:
        campaign.close()


def _report(snapshot: dict, settings: QwenSettings) -> dict:
    fields = (
        "campaign_id",
        "authorization_fingerprint",
        "price_fingerprint",
        "request_count",
        "charged_tokens",
        "charged_cny_upper_bound",
        "billing_basis",
        "entries",
    )
    report = {**snapshot["receipt"], **{key: snapshot[key] for key in fields}}
    # post_chat accepts a response only if the response model exactly matches settings.model.
    report["verified_response_model"] = (
        settings.model
        if any(entry["transport_status"] == "response_received" for entry in snapshot["entries"])
        else None
    )
    return report


def replay_programming_live(binding: ProgrammingLiveInput) -> dict:
    """Reopen the existing journal and independently reconstruct Facts and Witnesses."""
    return _replay(binding, origin="qwen_live")


def _replay(binding: ProgrammingLiveInput, *, origin: Literal["qwen_live", "http_mock"]) -> dict:
    source = binding.source()
    approval = binding.admission.approval
    directory = binding.admission.ledger_root
    if origin == "http_mock":
        directory /= "programming-offline-test"
    journal = directory / f"{approval.campaign_id}.jsonl"
    try:
        if journal.stat().st_size > 2_000_000:
            raise AgentError("programming_live_journal_invalid")
        snapshot = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])
        receipt = snapshot["receipt"]
        if (
            snapshot["origin"] != origin
            or snapshot["authorization_fingerprint"]
            != sha256(approval.model_dump_json().encode()).hexdigest()
            or receipt["state"] != "completed"
            or receipt["task_id"] != source.scope.task
            or receipt["snapshot_id"] != source.fingerprint
            or receipt["execution_origin"] != origin
        ):
            raise AgentError("programming_live_readback_unavailable")
        executor = ProgrammingExecutor(
            source, receipt["run_id"], _limits(binding), source.material.problems
        )
        choices = receipt["model_choices"]
        steps = [tool for tool in receipt["tool_ledger"] if tool["status"] == "accepted"]
        for tool in steps:
            facts = executor.execute(tool["tool"], executor.parse(tool["tool"], tool["arguments"]))
            if [fact.fact_id for fact in facts] != tool["fact_ids"]:
                raise AgentError("programming_live_readback_mismatch")
        last = choices[-1]
        if last["tool"] != "finish":
            raise AgentError("programming_live_readback_mismatch")
        result = _verified_result(
            executor, last["arguments"]["reason"], last["arguments"]["selected_fact_ids"]
        )
        source.validate()
        if result != receipt["result"] or _digest(result) != receipt["result_sha"]:
            raise AgentError("programming_live_readback_mismatch")
        return _report(snapshot, binding.settings)
    except (KeyError, IndexError, OSError, TypeError, AttributeError, json.JSONDecodeError):
        raise AgentError("programming_live_readback_unavailable") from None
