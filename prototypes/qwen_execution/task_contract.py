"""AI-side task binding and existing-session hook; no route or budget allocation."""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Literal

import httpx
from app.ai.edu.discovery.models import AgentError, Limits, Strict
from app.ai.edu.qwen_config import QwenSettings
from app.edu.auth import Principal
from pydantic import Field, model_validator

from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.capability_profiles.agent import ProfilePlanner, run_profile_session
from prototypes.capability_profiles.database import DatabaseProfileSource
from prototypes.capability_profiles.engine import build_profiles
from prototypes.durable_teacher_agent.store import TaskStore
from prototypes.teacher_agent_bridge.provider import DatabaseSource


@dataclass(frozen=True, repr=False)
class _DatabaseTestTransport:
    """Python-only capability; never parsed from a request or environment flag."""

    transport: httpx.MockTransport

    def check(self, budget: CampaignBudget) -> None:
        if type(self.transport) is not httpx.MockTransport:
            raise AgentError("database_test_mock_transport_required")
        if budget.origin != "http_mock":
            raise AgentError("database_test_mock_budget_required")


@dataclass(frozen=True, repr=False)
class ExternalizationScope:
    """Server-generated binding passed to an independently owned authorization check."""

    teacher_id: str
    task_id: str
    campaign_id: str
    batch_id: str
    snapshot_id: str
    source_sha256: str
    model: str
    endpoint: str
    purpose: Literal["assessment_aggregate_remote_inference"] = (
        "assessment_aggregate_remote_inference"
    )


@dataclass(frozen=True, repr=False)
class DatabaseTaskInput:
    """Backend supplies its EXISTING task/store/source/budget and a trusted gate.

    The gate must raise on missing/revoked/expired external-processing consent and
    independently reviewed model/code/price/fees. Never implement it as synthetic_only.
    It is server code, not a client callback or a boolean read from the request.
    """

    task_id: str
    batch_id: str
    snapshot_id: str
    actor: Principal = field(repr=False)
    store: TaskStore = field(repr=False)
    source: DatabaseSource = field(repr=False)
    budget: CampaignBudget = field(repr=False)
    settings: QwenSettings = field(repr=False)
    require_externalization: Callable[[ExternalizationScope], None] = field(repr=False)

    def check_binding(self) -> ExternalizationScope:
        if type(self.source) is not DatabaseSource or type(self.store) is not TaskStore:
            raise AgentError("trusted_database_task_required")
        row = self.store.get(self.actor, self.task_id)
        self.store.authorize(self.task_id, self.actor, running=True)
        if (
            row["batch_id"] != self.batch_id
            or row["snapshot_id"] != self.snapshot_id
            or self.source.envelope.actor != self.actor
            or self.source.envelope.batch_id != self.batch_id
            or self.source.envelope.bound_snapshot != self.snapshot_id
            or self.source.envelope.provider is not self.store.provider
            or self.source.fingerprint != row["source_sha"]
            or getattr(self.budget, "store", None) is not self.store
            or getattr(self.budget, "task_id", None) != self.task_id
            or self.budget.snapshot()["campaign_id"] != row["campaign_id"]
            or self.budget.snapshot()["source_fingerprints"]
            != {self.source.recipe: self.source.fingerprint}
        ):
            raise AgentError("database_task_binding_conflict")
        return ExternalizationScope(
            self.actor.teacher_id,
            self.task_id,
            row["campaign_id"],
            self.batch_id,
            self.snapshot_id,
            self.source.fingerprint,
            self.settings.model,
            self.settings.base_url,
        )

    def preflight(self, *, _testing: _DatabaseTestTransport | None = None) -> DatabaseProfileSource:
        """Run before aggregate construction AND again at every request/publication.

        Existing synthetic_mock TaskStore/DurableBudget are intentionally not upgraded
        by this function. Backend must install reviewed live persistence/admission first.
        """
        if _testing is not None:
            if type(_testing) is not _DatabaseTestTransport:
                raise AgentError("database_test_capability_required")
            _testing.check(self.budget)
        scope = self.check_binding()
        if not callable(self.require_externalization):
            raise AgentError("remote_processing_admission_required")
        self.require_externalization(scope)  # Consent check BEFORE reading score material.
        contract = DatabaseProfileSource(self.source)
        contract.validate()  # Original protocol, score/source and Witness validation.
        self.budget.check(self.settings, self.source)
        self.store.authorize(self.task_id, self.actor, running=True)
        self.require_externalization(scope)  # Revocation during the read fails closed.
        if _testing is None and self.budget.origin != "qwen_live":
            raise AgentError("database_live_budget_not_installed")
        return contract


class DatabasePlanner(ProfilePlanner):
    """Same planning/execution kernel; database permission is checked at each send."""

    def __init__(self, *args, binding: DatabaseTaskInput, _testing=None, **kwargs):
        self.binding = binding
        self._testing = _testing
        super().__init__(*args, **kwargs)

    def _admit_execution(self, budget, transport, allow_network):
        expected = None if self._testing is None else self._testing.transport
        if (
            budget is not self.binding.budget
            or transport is not expected
            or allow_network != (self._testing is None)
        ):
            raise AgentError("database_execution_binding_conflict")
        self.binding.preflight(_testing=self._testing)

    async def _post(self, payload: dict) -> dict:
        self.binding.preflight(_testing=self._testing)
        response = await super()._post(payload)
        self.binding.preflight(_testing=self._testing)
        return response


class DatabaseExecution:
    """Controlled adapter accepted by the existing single profile session."""

    def __init__(self, binding: DatabaseTaskInput, *, _testing=None):
        self.binding = binding
        self._testing = _testing
        self.settings = binding.settings
        self.planner = partial(DatabasePlanner, binding=binding, _testing=_testing)

    def check(self, source, budget, transport):
        if (
            source is not self.binding.source
            or budget is not self.binding.budget
            or transport is not (None if self._testing is None else self._testing.transport)
        ):
            raise AgentError("database_execution_binding_conflict")
        self.binding.preflight(_testing=self._testing)


async def run_database_profiles(binding: DatabaseTaskInput, *, limits: Limits) -> tuple:
    """Backend hook; consumes only its existing budget and returns its proof executor.

    Current Mock-only backend fails preflight. This function grants no remote consent.
    Backend owns persistence, cancellation and failure receipts, using the same TaskStore.
    """
    return await _run_database_profiles(binding, limits=limits)


async def _run_database_profiles(
    binding: DatabaseTaskInput, *, limits: Limits, _testing: _DatabaseTestTransport | None = None
) -> tuple:
    """One implementation for the unchanged Live entry and the explicit test seam."""
    if type(binding) is not DatabaseTaskInput:
        raise AgentError("trusted_database_task_required")
    contract = binding.preflight(_testing=_testing)
    with binding.source.validation_scope():
        book = build_profiles(contract, minimum_cell=limits.minimum_cell)
        result, book, executor = await run_profile_session(
            binding.source,
            book,
            binding.budget,
            limits=limits,
            transport=None if _testing is None else _testing.transport,
            reviewed_execution=DatabaseExecution(binding, _testing=_testing),
            retain_partial=False,
        )
        binding.preflight(_testing=_testing)
        executor.verify_all()
        binding.store.provider.replay_witnesses(
            binding.source,
            [
                ref
                for fact in executor.facts.values()
                for source in fact.item_sources
                for ref in source.references
            ],
        )
    # Fresh capture on scope exit and current authorization before release.
    binding.preflight(_testing=_testing)
    result["source_replay_verified"] = True
    return result, book, executor


class ExecutionProvenance(Strict):
    """Separate from immutable statistics/proofs; backend must not relabel live as Mock."""

    origin: Literal["qwen_live", "http_mock", "not_run"]
    state: Literal["not_run", "completed", "failed"]
    request_state: Literal["not_run", "attempted", "responses_received", "failed"]
    model_requests: int = Field(ge=0, le=6)
    tool_attempts: int = Field(ge=0, le=5)
    source_replay_verified: bool
    error_code: str | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.state == "not_run" and (
            self.origin != "not_run"
            or self.request_state != "not_run"
            or self.model_requests
            or self.tool_attempts
            or self.error_code
        ):
            raise ValueError("unrun_state_has_execution_evidence")
        if self.origin == "not_run" and (self.model_requests or self.tool_attempts):
            raise ValueError("unrun_execution_has_attempts")
        if self.request_state == "not_run" and self.model_requests:
            raise ValueError("unrun_request_has_attempts")
        if self.request_state != "not_run" and not self.model_requests:
            raise ValueError("request_evidence_missing")
        if self.state == "completed" and (
            self.error_code
            or not self.source_replay_verified
            or self.request_state != "responses_received"
        ):
            raise ValueError("verified_completion_required")
        if self.state == "failed" and not self.error_code:
            raise ValueError("explicit_failure_required")
        return self
