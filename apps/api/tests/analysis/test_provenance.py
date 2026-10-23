from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction

import pytest

from app.analysis.edu import (
    AnalysisInputError,
    AssessmentRecord,
    Condition,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    Operator,
    ScorePolicy,
    prepare_dataset,
    select_students,
    spearman_correlations,
    student_profile,
    summarize,
)


def test_results_preserve_the_exact_input_source_score_and_evidence(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    rows = []
    for index, score in enumerate((2, 3, 4)):
        source = f"synthetic-source-{index}"
        evidence = (
            EvidenceRef(
                source, f"synthetic-row-{index}/score", EvidenceKind.SCORE, Dimension.ALGORITHMS
            ),
            EvidenceRef(source, f"synthetic-row-{index}/comment", EvidenceKind.COMMENT),
        )
        rows.append(
            replace(
                make_record(
                    f"synthetic-{index}",
                    overrides={Dimension.ALGORITHMS: score, Dimension.IMPLEMENTATION: 5 - index},
                ),
                source_id=source,
                record_id=f"source-row-{index}",
                evidence_refs=evidence,
            )
        )
    data = prepare_dataset(reversed(rows), policy=ScorePolicy())
    scope = summarize(data, batch_id="synthetic-batch").scope
    pairs = spearman_correlations(data, batch_id="synthetic-batch").pairs[0].observations
    for index, original in enumerate(rows):
        ref = scope.records[index]
        assert (ref.student_id, ref.record_id, ref.source_id, ref.evidence_refs) == (
            original.student_id,
            original.record_id,
            original.source_id,
            original.evidence_refs,
        )
        point = pairs[index]
        assert point.reference == ref
        assert point.left_score == original.scores[Dimension.ALGORITHMS]
        assert point.right_score == original.scores[Dimension.IMPLEMENTATION]
        profile = student_profile(data, batch_id=original.batch_id, student_id=original.student_id)
        assert profile.reference == ref
        assert profile.dimensions[0].evidence_refs == original.evidence_refs
    selected = select_students(
        data,
        batch_id="synthetic-batch",
        conditions=[Condition(Dimension.ALGORITHMS, Operator.LT, Fraction(3))],
    )
    assert selected.matches == (scope.records[0],)
    assert selected.matches[0].evidence_refs == rows[0].evidence_refs


def test_no_evidence_is_invented(make_record: Callable[..., AssessmentRecord]) -> None:
    row = replace(make_record("synthetic-none"), evidence_refs=())
    data = prepare_dataset([row], policy=ScorePolicy())
    profile = student_profile(data, batch_id=row.batch_id, student_id=row.student_id)
    assert profile.reference.evidence_refs == ()
    assert all(dimension.evidence_refs == () for dimension in profile.dimensions)


def test_bad_evidence_is_a_structural_error(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    row = make_record("synthetic-a")
    malformed = (
        replace(row, evidence_refs=("not-a-reference",)),
        replace(row, evidence_refs=(EvidenceRef("", "row/score", EvidenceKind.SCORE),)),
        replace(row, evidence_refs=(EvidenceRef("source", "row/score", "score"),)),
    )
    for record in malformed:
        with pytest.raises(AnalysisInputError):
            prepare_dataset([record], policy=ScorePolicy())
