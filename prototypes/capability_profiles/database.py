"""Explicit verified-DB profile contract, never a synthetic recipe or generic Envelope."""

from dataclasses import dataclass, field

from prototypes.teacher_agent_bridge.provider import (
    DatabaseEnvelope,
    DatabaseProvider,
    DatabaseSource,
    ProductionDatabaseProvider,
)

from app.ai.edu.discovery.models import AgentError
from app.importing.edu.types import DIMENSIONS


@dataclass(frozen=True)
class DatabaseProfileSource:
    source: DatabaseSource = field(repr=False)

    @property
    def envelope(self):
        return self.source.envelope

    @property
    def fingerprint(self) -> str:
        return self.source.fingerprint

    @property
    def assessment_snapshot_id(self) -> str:
        return self.envelope.bound_snapshot

    def validate(self) -> None:
        # Import only on use: the durable host also consumes this adapter.
        from prototypes.durable_teacher_agent.service import AuthorizedEnvelope

        if type(self.source) is not DatabaseSource or type(self.envelope) not in (
            DatabaseEnvelope,
            AuthorizedEnvelope,
        ):
            raise AgentError("trusted_database_profile_source_required")
        if type(self.envelope.provider) not in (
            DatabaseProvider,
            ProductionDatabaseProvider,
        ):
            raise AgentError("trusted_database_profile_source_required")
        self.source.validate()
        data = self.envelope.data
        if (
            self.source.recipe != "authorized-db-" + self.envelope.batch_id
            or data.batch.batch_id != self.envelope.batch_id
            or data.version != self.assessment_snapshot_id
            or data.batch.version != self.assessment_snapshot_id
            or tuple(i.item_id for i in data.items) != tuple(f"I{i + 1:02}" for i in range(5))
            or tuple(i.confirmed_profile_code for i in data.items)
            != tuple(d.code for d in DIMENSIONS)
            or not 5 <= len(data.participants) <= 100
        ):
            raise AgentError("five_dimension_profile_source_required")
