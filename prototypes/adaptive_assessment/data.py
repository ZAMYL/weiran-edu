"""Reuse #23 entities, adding local byte-bound evidence witnesses, not a storage schema."""

from dataclasses import dataclass, field
from hashlib import sha256

from docs.canonical_assessment.model import Dataset


class PrototypeError(ValueError):
    """Fixed error codes only. Source text is never an error message."""


@dataclass(frozen=True)
class Witness:
    reference_id: str
    source_id: str
    source_sha256: str
    locator: str
    entry_sha256: str
    item_id: str
    participant_id: str | None
    kind: str
    value: str | None = field(repr=False)
    content_sha256: str


@dataclass(frozen=True)
class Materialized:
    data: Dataset = field(repr=False)
    witnesses: tuple[Witness, ...] = field(repr=False)
    levels: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Envelope:
    material: Materialized = field(repr=False)
    source_bytes: bytes = field(repr=False)
    format: str

    @property
    def data(self) -> Dataset:
        return self.material.data

    @property
    def snapshot_id(self) -> str:
        return sha256(self.source_bytes).hexdigest()

    def validate(self) -> None:
        from .adapters import decode

        self.data.validate()
        # A supplied evidence ID/hash/locator is not trusted: re-materialize actual bytes.
        expected = decode(self.source_bytes, self.format)
        if expected != self.material:
            raise PrototypeError("source_binding_mismatch")
