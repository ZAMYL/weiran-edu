"""Server-only verified SQLite seam. No Planner, remote model or client-JSON admission."""

import json
from dataclasses import dataclass, field
from hashlib import sha256
from threading import RLock
from uuid import uuid4

from app.ai.edu.discovery.disclosure import check_disclosure
from app.ai.edu.discovery.models import AgentError
from app.core.errors import APIError
from app.importing.edu.questions import PROTOCOL, Question, QuestionScore

from .auth import Principal
from .questions import QuestionService
from .service import EduService


@dataclass(frozen=True, repr=False)
class SourceWitness:
    reference_id: str
    batch_id: str
    snapshot_id: str
    source_sha256: str
    participant_id: str
    item_id: str
    sheet: str
    cell: str
    observation: QuestionScore = field(repr=False)


@dataclass(frozen=True, repr=False)
class AssessmentSnapshot:
    batch_id: str
    snapshot_id: str
    source_sha256: str
    scope_sha256: str
    items: tuple[Question, ...]
    participants: tuple[str, ...]
    observations: tuple[QuestionScore, ...]
    witnesses: tuple[SourceWitness, ...]
    rule_version: str = PROTOCOL
    origin: str = "authorized_sqlite_original_bytes_verified"
    remote_model_allowed: bool = False


@dataclass(frozen=True, repr=False)
class SnapshotLease:
    lease_id: str
    actor: Principal = field(repr=False)
    batch_id: str
    snapshot_id: str
    scope_sha256: str
    source_sha256: str
    purpose: str = "local_deterministic_analysis"


class _State:
    def __init__(self) -> None:
        self.lock = RLock()
        self.leases: dict[str, SnapshotLease] = {}
        self.histories: dict[tuple[str, str, str, str], list[frozenset[str]]] = {}


_initialization = RLock()


class QuestionAssessmentProvider:
    """One trusted service instance owns leases and cumulative disclosure across probes.

    This is local deterministic integration only. Multi-process durable disclosure,
    remote admission, formal Agent DTO mapping and five-dimension projection are absent.
    A fresh service cannot accept an old lease. No caller supplies a source/envelope.
    """

    def __init__(self, backend: EduService) -> None:
        self.backend = backend
        self.reader = QuestionService(backend)
        with _initialization:
            if not hasattr(backend, "_question_provider_state"):
                backend._question_provider_state = _State()
            self.state: _State = backend._question_provider_state

    def _load(self, actor: Principal, batch_id: str, snapshot_id: str) -> AssessmentSnapshot:
        if type(actor) is not Principal:
            raise APIError(403, "PROVIDER_PRINCIPAL_REQUIRED", "需要服务端教师主体")
        with self.backend.database.transaction() as connection:
            row, parsed, observations = self.reader._batch(connection, actor, batch_id, snapshot_id)
            scope = sha256(
                json.dumps(
                    {
                        "batch": batch_id,
                        "snapshot": snapshot_id,
                        "participants": sorted(parsed.participants),
                        "items": [i.item_id for i in parsed.questions],
                        "purpose": "local_deterministic_analysis",
                        "rule": PROTOCOL,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            index = {(o.participant_id, o.item_id): o for o in parsed.observations}
            witnesses = tuple(
                SourceWitness(
                    r["evidence_id"],
                    batch_id,
                    row["snapshot_id"],
                    parsed.file_sha256,
                    r["participant_id"],
                    r["item_id"],
                    "Sheet2",
                    r["source_cell"],
                    index[(r["participant_id"], r["item_id"])],
                )
                for r in observations
            )
            return AssessmentSnapshot(
                batch_id,
                row["snapshot_id"],
                parsed.file_sha256,
                scope,
                parsed.questions,
                parsed.participants,
                parsed.observations,
                witnesses,
            )

    def acquire(
        self,
        actor: Principal,
        batch_id: str,
        snapshot_id: str,
        *,
        purpose: str = "local_deterministic_analysis",
    ) -> SnapshotLease:
        if purpose != "local_deterministic_analysis":
            raise APIError(403, "FORMAL_MODEL_CALL_DISABLED", "正式测评数据仅允许本地确定性分析")
        with self.state.lock:
            snapshot = self._load(actor, batch_id, snapshot_id)
            lease = SnapshotLease(
                str(uuid4()),
                actor,
                batch_id,
                snapshot_id,
                snapshot.scope_sha256,
                snapshot.source_sha256,
            )
            self.state.leases[lease.lease_id] = lease
            self.state.histories.setdefault(self._key(lease), [frozenset(snapshot.participants)])
            return lease

    @staticmethod
    def _key(lease: SnapshotLease) -> tuple[str, str, str, str]:
        return (lease.actor.teacher_id, lease.batch_id, lease.snapshot_id, lease.purpose)

    def _current(self, lease: SnapshotLease) -> AssessmentSnapshot:
        if type(lease) is not SnapshotLease or self.state.leases.get(lease.lease_id) is not lease:
            raise APIError(403, "PROVIDER_LEASE_INVALID", "服务端快照引用无效")
        snapshot = self._load(lease.actor, lease.batch_id, lease.snapshot_id)
        if (snapshot.scope_sha256, snapshot.source_sha256) != (
            lease.scope_sha256,
            lease.source_sha256,
        ):
            raise APIError(409, "SNAPSHOT_MISMATCH", "快照范围已变化")
        return snapshot

    def read_snapshot(self, lease: SnapshotLease) -> AssessmentSnapshot:
        with self.state.lock:
            return self._current(lease)

    def release(self, lease: SnapshotLease) -> None:
        """Cancellation closes this lease without resetting previous disclosure."""
        with self.state.lock:
            if type(lease) is SnapshotLease and self.state.leases.get(lease.lease_id) is lease:
                del self.state.leases[lease.lease_id]

    def replay_witness(self, lease: SnapshotLease, reference_id: str) -> SourceWitness:
        with self.state.lock:
            snapshot = self._current(lease)
            witness = next((w for w in snapshot.witnesses if w.reference_id == reference_id), None)
            if witness is None:
                raise APIError(404, "RESOURCE_NOT_FOUND", "来源引用不存在")
            return witness

    def commit_disclosure(
        self, lease: SnapshotLease, reference_groups: tuple[tuple[str, ...], ...]
    ) -> str:
        """Aggregate exposure precheck, not a raw-data/model permission grant."""
        if type(reference_groups) is not tuple or len(reference_groups) > 32:
            raise APIError(422, "PROVIDER_DISCLOSURE_INVALID", "披露范围不符合要求")
        with self.state.lock:
            snapshot = self._current(lease)
            refs = {w.reference_id: w for w in snapshot.witnesses}
            groups = []
            for group in reference_groups:
                if (
                    type(group) is not tuple
                    or len(group) > len(refs)
                    or any(type(ref) is not str or ref not in refs for ref in group)
                ):
                    raise APIError(422, "PROVIDER_DISCLOSURE_INVALID", "来源引用无效")
                groups.append(frozenset(refs[ref].participant_id for ref in group))
            history = self.state.histories[self._key(lease)]
            if len(history) + len(groups) > 256:
                raise APIError(413, "PROVIDER_DISCLOSURE_LIMIT", "披露预算已耗尽")
            try:
                pending = check_disclosure(groups, released=history, staged=[], minimum_cell=5)
                # Also reject small atoms produced by several individually large sets.
                atoms = [frozenset(snapshot.participants)]
                for group in history + pending:
                    atoms = [
                        part for atom in atoms for part in (atom & group, atom - group) if part
                    ]
                    if any(len(atom) < 5 for atom in atoms):
                        raise AgentError("privacy_limited")
            except AgentError:
                raise APIError(
                    422, "PROVIDER_PRIVACY_LIMITED", "累计披露不满足最小群体约束"
                ) from None
            history.extend(pending)
            return sha256(json.dumps([sorted(g) for g in history]).encode()).hexdigest()
