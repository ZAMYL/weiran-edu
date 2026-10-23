from collections.abc import Callable, Mapping

import pytest

from app.analysis.edu import (
    AssessmentRecord,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    ScorePolicy,
    prepare_dataset,
)
from app.analysis.edu.models import Dataset, RawScore


@pytest.fixture
def make_record() -> Callable[..., AssessmentRecord]:
    def make(
        student: str,
        values: tuple[RawScore, ...] = (1, 1, 1, 1, 1),
        *,
        batch: str = "synthetic-batch",
        record_id: str | None = None,
        overrides: Mapping[Dimension, RawScore] | None = None,
    ) -> AssessmentRecord:
        scores = dict(zip(Dimension, values, strict=True))
        scores.update(overrides or {})
        return AssessmentRecord(
            batch,
            student,
            record_id or f"row-{student}",
            "synthetic-source",
            scores,
            (
                EvidenceRef(
                    "synthetic-source", f"{student}/score", EvidenceKind.SCORE, Dimension.ALGORITHMS
                ),
                EvidenceRef("synthetic-comments", f"{student}/comment", EvidenceKind.COMMENT),
                EvidenceRef(
                    "synthetic-source", f"{student}/basis", EvidenceKind.BASIS, Dimension.STRATEGY
                ),
            ),
        )

    return make


@pytest.fixture
def records(make_record: Callable[..., AssessmentRecord]) -> tuple[AssessmentRecord, ...]:
    return (
        make_record("synthetic-001", (2, 4, 3, 2, 4)),
        make_record("synthetic-002", (2.5, 3, 4, 1.5, 5)),
        make_record("synthetic-003", (3, 2, 2, 3, 3)),
        make_record("synthetic-004", (4, 5, 4.5, 2, 4.5)),
    )


@pytest.fixture
def dataset(records: tuple[AssessmentRecord, ...]) -> Dataset:
    return prepare_dataset(records, policy=ScorePolicy())
