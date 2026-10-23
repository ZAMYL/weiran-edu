from collections import Counter
from collections.abc import Iterable, Sequence
from fractions import Fraction
from operator import eq, ge, gt, le, lt
from statistics import median

from .models import (
    AnalysisInputError,
    Condition,
    CountShare,
    Dataset,
    Dimension,
    DimensionStats,
    Frequency,
    Operator,
    ProfileDimension,
    Scope,
    ScoreState,
    Selection,
    StudentProfile,
    Summary,
    ValidatedRecord,
    require_id,
)

COMPARATORS = {Operator.LT: lt, Operator.LE: le, Operator.EQ: eq, Operator.GE: ge, Operator.GT: gt}


def cohort(
    dataset: Dataset, batch_id: str, student_ids: Iterable[str] | None
) -> tuple[Scope, tuple[ValidatedRecord, ...]]:
    require_id(batch_id)
    batch = tuple(record for record in dataset.records if record.reference.batch_id == batch_id)
    requested = None
    if student_ids is not None:
        if isinstance(student_ids, str):
            raise AnalysisInputError("student_ids_must_be_a_collection")
        supplied = tuple(student_ids)
        for student_id in supplied:
            require_id(student_id)
        requested_set = set(supplied)
        requested = tuple(sorted(requested_set))
        known = {record.reference.student_id for record in batch}
        if not requested_set <= known:
            raise AnalysisInputError("unknown_student_in_cohort")
        batch = tuple(record for record in batch if record.reference.student_id in requested_set)
    scope = Scope(batch_id, dataset.policy, requested, tuple(record.reference for record in batch))
    return scope, batch


def dimension_stats(records: Sequence[ValidatedRecord], dimension: Dimension) -> DimensionStats:
    valid = tuple(record for record in records if record.score(dimension).state is ScoreState.VALID)
    values = tuple(record.score(dimension).value for record in valid)
    # Validated scores always carry a finite Fraction value.
    numeric = tuple(value for value in values if value is not None)
    counts = Counter(numeric)
    return DimensionStats(
        dimension=dimension,
        valid_count=len(valid),
        missing_count=sum(
            record.score(dimension).state is ScoreState.MISSING for record in records
        ),
        invalid_count=sum(
            record.score(dimension).state is ScoreState.INVALID for record in records
        ),
        mean=sum(numeric, Fraction(0)) / len(numeric) if numeric else None,
        median=median(numeric) if numeric else None,
        minimum=min(numeric) if numeric else None,
        maximum=max(numeric) if numeric else None,
        distribution=tuple(
            Frequency(value, CountShare(counts[value], len(valid))) for value in sorted(counts)
        ),
        valid_records=tuple(record.reference for record in valid),
    )


def summarize(
    dataset: Dataset, *, batch_id: str, student_ids: Iterable[str] | None = None
) -> Summary:
    scope, records = cohort(dataset, batch_id, student_ids)
    return Summary(scope, tuple(dimension_stats(records, dimension) for dimension in Dimension))


def select_students(
    dataset: Dataset,
    *,
    batch_id: str,
    conditions: Iterable[Condition],
    student_ids: Iterable[str] | None = None,
) -> Selection:
    query = tuple(conditions)
    if not query or any(not isinstance(condition, Condition) for condition in query):
        raise AnalysisInputError("nonempty_typed_conditions_required")
    scope, records = cohort(dataset, batch_id, student_ids)
    matches = []
    unevaluable = []
    for record in records:
        if any(
            record.score(condition.dimension).state is not ScoreState.VALID for condition in query
        ):
            unevaluable.append(record.reference)
            continue
        if all(
            COMPARATORS[condition.operator](
                record.score(condition.dimension).value, condition.threshold
            )
            for condition in query
        ):
            matches.append(record.reference)
    return Selection(scope, query, tuple(matches), tuple(unevaluable))


def student_profile(
    dataset: Dataset,
    *,
    batch_id: str,
    student_id: str,
    student_ids: Iterable[str] | None = None,
) -> StudentProfile:
    require_id(student_id)
    scope, records = cohort(dataset, batch_id, student_ids)
    target = next((record for record in records if record.reference.student_id == student_id), None)
    if target is None:
        raise AnalysisInputError("student_not_in_cohort")
    dimensions = []
    for dimension in Dimension:
        score = target.score(dimension)
        stats = dimension_stats(records, dimension)
        difference = (
            score.value - stats.mean if score.value is not None and stats.mean is not None else None
        )
        refs = tuple(
            ref
            for ref in target.reference.evidence_refs
            if ref.dimension is None or ref.dimension is dimension
        )
        dimensions.append(ProfileDimension(score, stats, difference, refs))
    return StudentProfile(scope, target.reference, tuple(dimensions))
