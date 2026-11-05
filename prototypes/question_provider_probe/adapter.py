"""Local-only compatibility probe; never a SyntheticAccess/AI permission adapter."""

import json
from dataclasses import dataclass, field
from hashlib import sha256

from app.edu.question_provider import AssessmentSnapshot, QuestionAssessmentProvider, SnapshotLease

from docs.canonical_assessment.model import (
    AssessmentItem,
    Batch,
    Dataset,
    Evidence,
    Observation,
    Participant,
    Scale,
)
from prototypes.adaptive_assessment.data import Envelope, Materialized, PrototypeError, Witness


def materialize(snapshot: AssessmentSnapshot):
    if (
        snapshot.remote_model_allowed
        or snapshot.origin != "authorized_sqlite_original_bytes_verified"
    ):
        raise PrototypeError("question_local_source_required")
    if any(o.state not in ("valid", "missing") for o in snapshot.observations):
        # Do not silently coerce invalid raw text to a legitimate missing observation.
        raise PrototypeError("question_invalid_state_mapping_not_implemented")
    manifest = json.dumps(
        {
            "batch": snapshot.batch_id,
            "snapshot": snapshot.snapshot_id,
            "source": snapshot.source_sha256,
            "scope": snapshot.scope_sha256,
            "rule": snapshot.rule_version,
        },
        sort_keys=True,
    ).encode()
    source_hash = sha256(manifest).hexdigest()
    index = {(w.participant_id, w.item_id): w for w in snapshot.witnesses}
    if len(index) != len(snapshot.observations) or len(index) != len(snapshot.witnesses):
        raise PrototypeError("question_witness_cardinality_mismatch")
    evidence, observations, witnesses = [], [], []
    for o in snapshot.observations:
        w = index.get((o.participant_id, o.item_id))
        if (
            w is None
            or w.observation != o
            or (w.batch_id, w.snapshot_id, w.source_sha256)
            != (snapshot.batch_id, snapshot.snapshot_id, snapshot.source_sha256)
        ):
            raise PrototypeError("question_witness_scope_mismatch")
        evidence.append(
            Evidence(
                w.reference_id,
                snapshot.source_sha256,
                "score",
                o.item_id,
                o.participant_id,
                ("teacher_analysis",),
                "retrieved",
            )
        )
        observations.append(
            Observation(
                "observation-" + w.reference_id,
                o.participant_id,
                o.item_id,
                o.raw,
                (w.reference_id,),
            )
        )
        for ref, kind in (
            (w.reference_id, "score"),
            ("row-" + o.participant_id + "-" + o.item_id, "row"),
        ):
            witnesses.append(
                Witness(
                    ref,
                    snapshot.source_sha256,
                    source_hash,
                    w.sheet + "!" + w.cell,
                    snapshot.source_sha256,
                    o.item_id,
                    o.participant_id,
                    kind,
                    o.raw,
                    sha256((o.raw or "<missing>").encode()).hexdigest(),
                )
            )
    data = Dataset(
        "question-" + snapshot.batch_id,
        snapshot.rule_version,
        Batch(snapshot.batch_id, snapshot.snapshot_id, (snapshot.source_sha256,)),
        tuple(Participant(p, snapshot.scope_sha256) for p in snapshot.participants),
        tuple(
            AssessmentItem(
                i.item_id,
                i.original_name,
                "question",
                Scale("scale-" + i.item_id, i.minimum, i.maximum, i.step, i.unit),
            )
            for i in snapshot.items
        ),
        tuple(observations),
        tuple(evidence),
    )
    data.validate()
    # Numeric bounds do not authorize us to invent ordinal/interval semantics.
    return Materialized(
        data, tuple(witnesses), tuple((i.item_id, "unknown") for i in snapshot.items)
    ), manifest


@dataclass(frozen=True)
class LocalQuestionEnvelope(Envelope):
    provider: QuestionAssessmentProvider = field(repr=False)
    lease: SnapshotLease = field(repr=False)

    def validate(self):
        if type(self.provider) is not QuestionAssessmentProvider:
            raise PrototypeError("question_provider_identity_required")
        current = self.provider.read_snapshot(self.lease)
        expected, manifest = materialize(current)
        if self.material != expected or self.source_bytes != manifest:
            raise PrototypeError("question_source_binding_mismatch")


def admit(provider: QuestionAssessmentProvider, lease: SnapshotLease):
    if type(provider) is not QuestionAssessmentProvider:
        raise PrototypeError("question_provider_identity_required")
    snapshot = provider.read_snapshot(lease)
    material, manifest = materialize(snapshot)
    envelope = LocalQuestionEnvelope(material, manifest, "local-question-provider", provider, lease)
    envelope.validate()
    return envelope
