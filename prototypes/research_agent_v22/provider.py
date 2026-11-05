"""Typed server Provider seam, implemented only for one closed synthetic source."""

import json
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Protocol

from app.ai.edu.discovery.models import AgentError, Limits

from prototypes.adaptive_assessment.analysis import VERSION
from prototypes.adaptive_assessment.data import Envelope
from prototypes.adaptive_assessment.synthetic import AdmittedSynthetic, admit
from prototypes.research_agent_v2.goals import understand
from prototypes.research_agent_v2.runtime import ResearchExecutor

from .preflight import RECIPE


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Binding:
    actor: str = field(repr=False)
    batch_id: str
    snapshot_id: str
    scope_sha256: str
    source_sha256: str
    authorization_revision: int
    campaign_id: str
    disclosure_sha256: str
    purpose: str = "offline_synthetic_provider_probe"
    rule_version: str = VERSION


class AssessmentSnapshot(Protocol):
    fingerprint: str
    envelope: Envelope

    def validate(self) -> None: ...


def scope_hash(data) -> str:
    return digest(
        {
            "participants": [p.participant_id for p in data.participants],
            "items": [i.item_id for i in data.items],
            "purpose": "offline_synthetic_provider_probe",
        }
    )


@dataclass(frozen=True)
class Lease:
    binding: Binding
    source: AssessmentSnapshot = field(repr=False)


class AssessmentProvider(Protocol):
    def acquire(self, actor: str, batch_id: str, snapshot_id: str, campaign_id: str) -> Lease: ...
    def assert_current(self, binding: Binding) -> None: ...
    def commit_disclosure(self, binding: Binding, released: list) -> Binding: ...
    def reserve_tool(self, binding: Binding) -> None: ...
    def replay_witness(self, binding: Binding, reference: str): ...


class SyntheticProvider:
    """No file/JSON admission, SQLite access, real authentication or remote model access."""

    def __init__(self):
        self.source = admit(RECIPE)
        self.revision = 1
        self.enabled = True
        ids = [p.participant_id for p in self.source.envelope.data.participants]
        self._scope = scope_hash(self.source.envelope.data)
        self._history = [frozenset(ids)]
        self._active = None
        self._attempts = 0

    def _history_hash(self):
        return digest([sorted(group) for group in self._history])

    def acquire(self, actor, batch_id, snapshot_id, campaign_id) -> Lease:
        data = self.source.envelope.data
        if actor != "synthetic-teacher" or not self.enabled:
            raise AgentError("provider_access_denied")
        if batch_id != data.batch.batch_id or snapshot_id != self.source.fingerprint:
            raise AgentError("provider_snapshot_mismatch")
        if self._active is not None:
            raise AgentError("provider_campaign_already_bound")
        binding = Binding(
            actor,
            batch_id,
            snapshot_id,
            self._scope,
            self.source.fingerprint,
            self.revision,
            campaign_id,
            self._history_hash(),
        )
        self._active = binding
        return Lease(binding, self.source)

    def assert_current(self, binding):
        if not self.enabled or binding.authorization_revision != self.revision:
            raise AgentError("provider_authorization_revoked")
        if binding != self._active or binding.disclosure_sha256 != self._history_hash():
            raise AgentError("provider_context_mismatch")
        self.source.validate()
        data = self.source.envelope.data
        if (
            binding.batch_id != data.batch.batch_id
            or binding.snapshot_id != self.source.fingerprint
            or binding.source_sha256 != self.source.fingerprint
            or binding.scope_sha256 != scope_hash(data)
        ):
            raise AgentError("provider_source_scope_mismatch")

    def reserve_tool(self, binding):
        self.assert_current(binding)
        if self._attempts >= Limits().tool_attempts:
            raise AgentError("tool_budget_exhausted")
        self._attempts += 1

    def commit_disclosure(self, binding, released):
        self.assert_current(binding)
        from dataclasses import replace

        if released[: len(self._history)] != self._history:
            raise AgentError("provider_disclosure_history_mismatch")
        self._history = list(released)
        self._active = replace(binding, disclosure_sha256=self._history_hash())
        return self._active

    def replay_witness(self, binding, reference):
        self.assert_current(binding)
        witness = next(
            (w for w in self.source.envelope.material.witnesses if w.reference_id == reference),
            None,
        )
        if (
            witness is None
            or witness.source_sha256 != binding.source_sha256
            or witness.kind not in ("score", "row")
        ):
            raise AgentError("provider_unknown_witness")
        return witness


class ProviderProbe:
    """Demonstrate per-call ownership/revocation/source/disclosure checks with real tools."""

    def __init__(self, provider: AssessmentProvider, lease: Lease):
        provider.assert_current(lease.binding)
        if type(lease.source) is not AdmittedSynthetic:
            raise AgentError("non_synthetic_provider_not_enabled")
        lease.source.validate()
        data = lease.source.envelope.data
        if (
            lease.source.fingerprint != lease.binding.source_sha256
            or lease.source.fingerprint != lease.binding.snapshot_id
            or data.batch.batch_id != lease.binding.batch_id
            or scope_hash(data) != lease.binding.scope_sha256
        ):
            raise AgentError("provider_lease_source_mismatch")
        self.provider, self.binding = provider, lease.binding
        self.executor = ResearchExecutor(lease.source, "provider-probe", Limits())
        self._blocked = False

    def execute(self, name: str, arguments: dict):
        if self._blocked:
            raise AgentError("provider_probe_blocked")
        self.provider.reserve_tool(self.binding)
        e = self.executor
        before = (dict(e.facts), list(e.released), set(e.seen), set(e.identities), e.output_bytes)
        try:
            facts = e.execute(name, e.parse(name, arguments))
            for fact in facts:
                e._verify(fact)
            self.binding = self.provider.commit_disclosure(self.binding, e.released)
            return [f.view for f in facts]
        except BaseException:
            e.facts, e.released, e.seen, e.identities, e.output_bytes = before
            self._blocked = True
            raise

    def cards(self, fact_ids: list[str]):
        self.provider.assert_current(self.binding)
        cards = self.executor.finish(
            self.executor.parse(
                "finish",
                {
                    "selected_fact_ids": fact_ids,
                    "reason": "completed" if fact_ids else "no_valid_findings",
                },
            )
        )
        self.provider.assert_current(self.binding)
        return cards

    def goal(self, question: str):
        self.provider.assert_current(self.binding)
        return understand(question, self.executor.kernel.catalog()).view()

    def metadata(self):
        self.provider.assert_current(self.binding)
        return {
            "binding": {k: v for k, v in asdict(self.binding).items() if k != "actor"},
            "capabilities": self.executor.kernel.catalog(),
            "remote_model_allowed": False,
            "unimplemented": [
                "formal_teacher_auth",
                "cross_session_disclosure",
                "dynamic_http_dto",
            ],
        }
