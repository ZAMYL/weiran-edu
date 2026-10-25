"""Fixed, entirely synthetic demonstration; never loads teacher files or a database."""

from datetime import UTC, datetime
from fractions import Fraction

from app.analysis.edu import (
    AssessmentRecord,
    Dimension,
    Operator,
    ScorePolicy,
    prepare_dataset,
    select_students,
    summarize,
)
from app.analysis.edu import (
    Condition as AnalysisCondition,
)
from app.edu.analysis import CODE_MAP

from .models import (
    AnonymousStudent,
    Condition,
    DetailAuthorization,
    DimensionReport,
    Frequency,
    GroupGrant,
    GroupReport,
    ReportInput,
    Scale,
    Score,
    Source,
)


def synthetic_report(size: int = 17) -> ReportInput:
    if type(size) is not int or not 0 <= size <= 5000:
        raise ValueError("synthetic_demo_size_outside_limit")
    source = Source("synthetic-report-batch", f"synthetic-report-snapshot-v1-{size}")
    scale = Scale(Fraction(1), Fraction(5), Fraction(1, 100), "synthetic-rubric-v1")
    rows = []
    patterns = ((2, 4), (Fraction(11, 4), 5), (3, 4), (4, 3))
    for index in range(size):
        algorithm, strategy = patterns[index % len(patterns)]
        rows.append(
            AssessmentRecord(
                source.batch_id,
                f"synthetic-member-{index:04d}",
                f"synthetic-record-{index:04d}",
                "synthetic-source",
                {
                    dim: algorithm
                    if dim is Dimension.ALGORITHMS
                    else strategy
                    if dim is Dimension.STRATEGY
                    else 3
                    for dim in Dimension
                },
            )
        )
    dataset = prepare_dataset(rows, policy=ScorePolicy(step=Fraction(1, 100)))
    summary = summarize(dataset, batch_id=source.batch_id)
    conditions = (
        AnalysisCondition(Dimension.ALGORITHMS, Operator.LT, 3),
        AnalysisCondition(Dimension.STRATEGY, Operator.GE, 4),
    )
    selected = select_students(dataset, batch_id=source.batch_id, conditions=conditions)
    matched_summary = summarize(dataset, batch_id=source.batch_id, student_ids=selected.student_ids)

    def dimensions(summary):
        return tuple(
            DimensionReport(
                source,
                CODE_MAP[item.dimension],
                item.valid_count,
                item.missing_count,
                item.invalid_count,
                item.mean,
                item.median,
                item.minimum,
                item.maximum,
                tuple(Frequency(cell.score, cell.share.count) for cell in item.distribution),
                scale,
            )
            for item in summary.dimensions
        )

    matched = set(selected.student_ids)
    members = tuple(
        AnonymousStudent(
            source,
            row.reference.student_id,
            tuple(
                Score(
                    CODE_MAP[cell.dimension],
                    cell.value,
                    f"synthetic-evidence-{index}-{cell.dimension.name.lower()}",
                )
                for cell in row.scores
            ),
        )
        for index, row in enumerate(dataset.records)
        if row.reference.student_id in matched
    )
    group = GroupReport(
        source,
        "synthetic-selected-group",
        (
            Condition(CODE_MAP[Dimension.ALGORITHMS], "lt", Fraction(3)),
            Condition(CODE_MAP[Dimension.STRATEGY], "gte", Fraction(4)),
        ),
        len(selected.matches),
        summary.scope.student_count,
        selected.eligible_count,
        len(selected.unevaluable_records),
        selected.matched_share.proportion,
        selected.matched_among_eligible.proportion,
        "edu.synthetic.report.v1",
        members,
        dimensions(matched_summary),
    )
    return ReportInput(
        source,
        datetime(2026, 10, 25, tzinfo=UTC),
        summary.scope.student_count,
        dimensions(summary),
        "edu.synthetic.report.v1",
        (group,),
        synthetic=True,
    )


def synthetic_authorization(report: ReportInput) -> DetailAuthorization:
    """Only for this built-in demo, not a helper for authorizing production inputs."""
    if report != synthetic_report(report.record_count):
        raise ValueError("synthetic_demo_only")
    return DetailAuthorization(
        report.source,
        tuple(
            GroupGrant(g.group_id, frozenset(row.student_id for row in g.members))
            for g in report.groups
        ),
    )
