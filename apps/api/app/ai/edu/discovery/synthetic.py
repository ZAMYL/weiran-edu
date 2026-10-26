"""Closed built-in synthetic recipes. No files, arbitrary datasets or environment access."""

import re
from fractions import Fraction

from app.analysis.edu import prepare_dataset
from app.analysis.edu.discovery import TargetedDiscovery
from app.analysis.edu.models import (
    AssessmentRecord,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    ScorePolicy,
)

from .models import AgentError

NAMES = (
    "coupled53",
    "constant17",
    "constant53",
    "rare105",
    "boundary30",
    "boundary53",
    "missing17",
    "empty",
)


def rows(name: str) -> tuple[tuple[object, ...], ...]:
    if name == "coupled53":
        return tuple((1, 5, 3, 3, 3) if i < 26 else (5, 1, 3, 3, 3) for i in range(53))
    if name in ("constant17", "constant53"):
        return ((3, 3, 3, 3, 3),) * int(name.removeprefix("constant"))
    if name == "rare105":
        return ((3, 3, 3, 3, 3),) * 100 + ((5, 1, 3, 3, 3),) * 5
    if name == "boundary30":
        return (
            ((2, 3, 3, 3, 4),) * 10
            + ((Fraction(11, 4), 3, 3, 3, 4),) * 10
            + ((4, 3, 3, 3, 4),) * 10
        )
    if name == "boundary53":
        return ((2, 3, 3, 3, 4),) * 4 + ((Fraction(11, 4), 3, 3, 3, 4),) + ((4, 3, 3, 3, 2),) * 48
    if name == "missing17":
        return ((None, 3, 3, 3, 3),) * 15 + (("invalid-synthetic", 3, 3, 3, 3),) * 2
    if name == "empty":
        return ()
    raise AgentError("unknown_synthetic_case")


def build(name: str, *, context: tuple[str, str, str] | None = None) -> TargetedDiscovery:
    batch, snapshot, source = "synthetic-batch", "synthetic-snapshot", "synthetic-source"
    if context is not None:
        if (
            not isinstance(context, tuple)
            or len(context) != 3
            or any(
                not isinstance(value, str) or not re.fullmatch(prefix + r"-[0-9a-f]{20}", value)
                for value, prefix in zip(
                    context,
                    ("synthetic-batch", "synthetic-snapshot", "synthetic-source"),
                    strict=True,
                )
            )
        ):
            raise AgentError("invalid_synthetic_context")
        batch, snapshot, source = context
    records = tuple(
        AssessmentRecord(
            batch,
            f"synthetic-member-{i:04}",
            f"synthetic-record-{i:04}",
            source,
            dict(zip(Dimension, row, strict=True)),
            tuple(
                EvidenceRef(source, f"synthetic-score-{i:04}-{d.name}", EvidenceKind.SCORE, d)
                for d in Dimension
            ),
        )
        for i, row in enumerate(rows(name))
    )
    dataset = prepare_dataset(records, policy=ScorePolicy(step=Fraction(1, 100)))
    return TargetedDiscovery(dataset, batch_id=batch, snapshot_id=snapshot)
