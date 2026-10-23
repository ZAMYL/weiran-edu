from collections.abc import Iterable, Mapping
from decimal import Decimal
from fractions import Fraction

from .models import (
    AnalysisInputError,
    AssessmentRecord,
    Dataset,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    RawScore,
    RecordRef,
    Score,
    ScoreIssue,
    ScorePolicy,
    ScoreState,
    ValidatedRecord,
    number,
    require_id,
)


def _score(dimension: Dimension, raw: RawScore, policy: ScorePolicy) -> Score:
    if raw is None or isinstance(raw, str) and raw in policy.missing_tokens:
        return Score(dimension, raw, None, ScoreState.MISSING, "missing")
    try:
        value = number(raw)
    except AnalysisInputError as error:
        return Score(dimension, raw, None, ScoreState.INVALID, str(error))
    if not policy.minimum <= value <= policy.maximum:
        return Score(dimension, raw, None, ScoreState.INVALID, "out_of_range")
    if policy.step is not None and ((value - policy.minimum) / policy.step).denominator != 1:
        return Score(dimension, raw, None, ScoreState.INVALID, "off_step")
    return Score(dimension, raw, value, ScoreState.VALID)


def _reference(record: AssessmentRecord) -> RecordRef:
    for identifier in (record.batch_id, record.student_id, record.record_id, record.source_id):
        require_id(identifier)
    refs = tuple(record.evidence_refs)
    for ref in refs:
        if not isinstance(ref, EvidenceRef):
            raise AnalysisInputError("typed_evidence_reference_required")
        require_id(ref.source_id)
        require_id(ref.location)
        if not isinstance(ref.kind, EvidenceKind) or (
            ref.dimension is not None and not isinstance(ref.dimension, Dimension)
        ):
            raise AnalysisInputError("invalid_evidence_reference")
    return RecordRef(record.batch_id, record.student_id, record.record_id, record.source_id, refs)


def prepare_dataset(records: Iterable[AssessmentRecord], *, policy: ScorePolicy) -> Dataset:
    """Reject structural ambiguity; report missing/invalid score cells without imputation."""
    if not isinstance(policy, ScorePolicy):
        raise AnalysisInputError("score_policy_required")
    seen_students: set[tuple[str, str]] = set()
    seen_sources: set[tuple[str, str, str]] = set()
    validated = []
    for record in records:
        if not isinstance(record, AssessmentRecord):
            raise AnalysisInputError("typed_assessment_record_required")
        reference = _reference(record)
        student_key = (record.batch_id, record.student_id)
        source_key = (record.batch_id, record.source_id, record.record_id)
        if student_key in seen_students:
            raise AnalysisInputError("duplicate_student_in_batch")
        if source_key in seen_sources:
            raise AnalysisInputError("duplicate_record_reference")
        seen_students.add(student_key)
        seen_sources.add(source_key)
        if not isinstance(record.scores, Mapping) or any(
            not isinstance(dimension, Dimension) for dimension in record.scores
        ):
            raise AnalysisInputError("unknown_score_dimension")
        raw_scores = dict(record.scores)
        if any(
            value is not None and not isinstance(value, (int, float, Decimal, Fraction, str))
            for value in raw_scores.values()
        ):
            raise AnalysisInputError("unsupported_score_cell_type")
        scores = tuple(
            _score(dimension, raw_scores.get(dimension), policy) for dimension in Dimension
        )
        validated.append(ValidatedRecord(reference, scores))
    ordered = tuple(
        sorted(
            validated, key=lambda record: (record.reference.batch_id, record.reference.student_id)
        )
    )
    issues = tuple(
        ScoreIssue(record.reference, score)
        for record in ordered
        for score in record.scores
        if score.state is not ScoreState.VALID
    )
    return Dataset(policy, ordered, issues)
