"""Reusable generated fixtures. Sizes and seeds belong to tests, not runtime settings."""

from dataclasses import dataclass
from decimal import Decimal
from random import Random

from app.analysis.edu import AssessmentRecord, Dimension, EvidenceKind, EvidenceRef
from app.analysis.edu.models import RawScore, ScoreState


@dataclass(frozen=True)
class ExpectedCell:
    units: int | None
    state: ScoreState


@dataclass(frozen=True)
class SyntheticBatch:
    batch_id: str
    rows: tuple[AssessmentRecord, ...]
    expected: dict[tuple[str, Dimension], ExpectedCell]


def make_batch(
    size: int,
    *,
    batch_id: str,
    seed: int = 87,
    with_issues: bool = False,
) -> SyntheticBatch:
    """Generate five dimensions in integer hundredths, plus explicitly declared bad cells."""
    if type(size) is not int or size < 0:
        raise ValueError("nonnegative_test_size_required")
    rng = Random(seed)
    anchors = (275, 299, 300, 301, 100, 500, 400, 399)
    strategies = (400, 500, 400, 400, 500, 100, 400, 401)
    rows, expected = [], {}
    for index in range(size):
        student_id = f"synthetic-student-{index:05d}"
        scores: dict[Dimension, RawScore] = {}
        for dimension_index, dimension in enumerate(Dimension):
            units = rng.randint(100, 500)
            if index < len(anchors):
                if dimension is Dimension.ALGORITHMS:
                    units = anchors[index]
                elif dimension is Dimension.STRATEGY:
                    units = strategies[index]
            raw: RawScore = Decimal(units).scaleb(-2)
            cell = ExpectedCell(units, ScoreState.VALID)
            marker = index + 3 * dimension_index
            if with_issues and marker % 11 == 0:
                raw, cell = None, ExpectedCell(None, ScoreState.MISSING)
            elif with_issues and marker % 13 == 0:
                raw, cell = Decimal("2.755"), ExpectedCell(None, ScoreState.INVALID)
            elif with_issues and marker % 17 == 0:
                raw, cell = Decimal("5.01"), ExpectedCell(None, ScoreState.INVALID)
            scores[dimension] = raw
            expected[student_id, dimension] = cell
        source_id = f"{batch_id}-synthetic-source"
        refs = tuple(
            EvidenceRef(
                source_id,
                f"synthetic-row-{index}/{dimension.name}/score",
                EvidenceKind.SCORE,
                dimension,
            )
            for dimension in Dimension
        ) + (
            EvidenceRef(
                source_id,
                f"synthetic-row-{index}/comment",
                EvidenceKind.COMMENT,
                Dimension.ALGORITHMS,
            ),
        )
        rows.append(
            AssessmentRecord(
                batch_id,
                student_id,
                f"synthetic-record-{index:05d}",
                source_id,
                scores,
                refs,
            )
        )
    return SyntheticBatch(batch_id, tuple(rows), expected)
