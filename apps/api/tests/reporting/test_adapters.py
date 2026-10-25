"""Merged DTO adaptation, complete pagination and privacy whitelist tests."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.edu.analysis import stat
from app.edu.analysis_models import (
    AnalysisScope,
    DimensionStatistics,
    Frequency,
    GroupResult,
    Statistics,
)
from app.edu.models import Rubric, RubricCriterion, RubricSource, ScoreScale, Student, StudentScore
from app.importing.edu.preview import Page
from app.importing.edu.types import DIMENSIONS
from app.reporting.edu import ReportError, from_statistics, generate_report
from app.reporting.edu.demo import synthetic_authorization, synthetic_report


def statistics(report, dims=None, *, total=None, kind="batch"):
    dims = report.dimensions if dims is None else dims
    total = report.record_count if total is None else total
    return Statistics(
        batch_id=report.source.batch_id,
        snapshot_id=report.source.snapshot_id,
        rule_version=report.rule_version,
        scope=AnalysisScope(kind=kind, record_count=total),
        dimensions=tuple(
            DimensionStatistics(
                dimension_code=d.code,
                valid_count=d.valid_count,
                missing_count=d.missing_count,
                invalid_count=d.invalid_count,
                mean=stat(d.mean),
                median=stat(d.median),
                minimum=stat(d.minimum),
                maximum=stat(d.maximum),
                distribution=tuple(
                    Frequency(
                        score=float(c.score),
                        count=c.count,
                        denominator=d.valid_count,
                        ratio=stat(Fraction(c.count, d.valid_count)),
                    )
                    for c in d.distribution
                ),
            )
            for d in dims
        ),
    )


def rubric(report):
    return Rubric(
        batch_id=report.source.batch_id,
        snapshot_id=report.source.snapshot_id,
        rubric_id="synthetic-rubric",
        rubric_version="synthetic-rubric-v1",
        score_policy=ScoreScale(),
        criteria=tuple(
            RubricCriterion(
                rubric_criterion_id=f"synthetic-criterion-{i}",
                dimension_code=s.code,
                display_name=s.label,
                original_name=s.rubric_title,
                source=RubricSource(
                    file_sha256="a" * 64, sheet_name="Sheet1", range=s.rubric_range
                ),
            )
            for i, s in enumerate(DIMENSIONS)
        ),
    )


def pages(report, *, page_size=2, include_statistics=True):
    group = report.groups[0]
    students = tuple(
        Student(
            student_id=row.student_id,
            display_label="不应导出的合成姓名 202600000001",
            scores=tuple(
                StudentScore(
                    dimension_code=s.dimension_code, score=float(s.value), evidence_id=s.evidence_id
                )
                for s in row.scores
            ),
        )
        for row in group.members
    )
    offsets = range(0, len(students), page_size) if students else (0,)
    return tuple(
        GroupResult(
            batch_id=report.source.batch_id,
            snapshot_id=report.source.snapshot_id,
            conditions=tuple(
                {
                    "dimension_code": c.dimension_code,
                    "operator": c.operator,
                    "value": Decimal(c.threshold.numerator) / Decimal(c.threshold.denominator),
                }
                for c in group.conditions
            ),
            rule_version=report.rule_version,
            matched_count=group.matched_count,
            total_count=group.total_count,
            ratio=float(group.ratio),
            eligible_count=group.eligible_count,
            unevaluable_count=group.unevaluable_count,
            matched_among_eligible_ratio=float(group.eligible_ratio),
            items=students[offset : offset + page_size],
            page=Page(
                offset=offset,
                limit=page_size,
                returned=len(students[offset : offset + page_size]),
                has_more=offset + page_size < len(students),
            ),
            statistics=statistics(
                report, group.statistics, total=group.matched_count, kind="matched_group"
            )
            if include_statistics
            else None,
        )
        for offset in offsets
    )


def adapt(report, group_pages=None, **kwargs):
    return from_statistics(
        statistics(report),
        generated_at=report.generated_at,
        rubric=rubric(report),
        group_pages=(pages(report) if group_pages is None else group_pages,),
        **kwargs,
    )


def test_full_pages_reassemble_complete_members_and_keep_precise_values():
    original = synthetic_report(17)
    result = adapt(original)
    assert result.record_count == 17
    assert len(result.groups[0].members) == original.groups[0].matched_count == 9
    assert result.groups[0].ratio == Fraction(9, 17)
    assert adapt(original, tuple(reversed(pages(original)))) == result
    # Purpose/grants originate outside the report, not from an HTTP credential inside it.
    from app.reporting.edu import DetailAuthorization, GroupGrant

    grant = DetailAuthorization(
        result.source,
        (
            GroupGrant(
                result.groups[0].group_id,
                frozenset(r.student_id for r in result.groups[0].members),
            ),
        ),
    )
    output = generate_report(result, include_details=True, authorization=grant)
    assert all(row.student_id in output.markdown for row in result.groups[0].members)
    assert "不应导出的合成姓名" not in output.markdown
    assert "202600000001" not in output.markdown


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p[:1],
        lambda p: (p[0], p[0], *p[1:]),
        lambda p: (p[0], *p[2:]),
        lambda p: (p[0].model_copy(update={"snapshot_id": "foreign-snapshot"}), *p[1:]),
        lambda p: (p[0].model_copy(update={"batch_id": "foreign-batch"}), *p[1:]),
        lambda p: (p[0].model_copy(update={"total_count": 105}), *p[1:]),
        lambda p: (p[0].model_copy(update={"ratio": 0.1}), *p[1:]),
        lambda p: (
            p[0].model_copy(update={"page": p[0].page.model_copy(update={"has_more": False})}),
            *p[1:],
        ),
        lambda p: (p[0], p[1].model_copy(update={"items": p[0].items}), *p[2:]),
    ],
)
def test_partial_drifted_or_duplicate_pages_are_rejected(mutation):
    report = synthetic_report(17)
    with pytest.raises(ReportError):
        adapt(report, mutation(pages(report)))


def test_wrong_rubric_and_statistics_snapshot_are_rejected():
    report = synthetic_report(4)
    with pytest.raises(ReportError, match="rubric_source_mismatch"):
        from_statistics(
            statistics(report),
            generated_at=report.generated_at,
            rubric=rubric(report).model_copy(update={"snapshot_id": "foreign"}),
        )
    wrong = pages(report)[0].statistics.model_copy(update={"snapshot_id": "foreign"})
    with pytest.raises(ReportError, match="statistics_scope_mismatch"):
        adapt(report, (pages(report)[0].model_copy(update={"statistics": wrong}),))


def test_display_raw_text_and_extra_private_fields_are_never_copied():
    report = synthetic_report(4)
    dto = statistics(report)
    values = list(dto.dimensions)
    private = '=HYPERLINK("https://example.invalid","合成姓名 202600000001")'
    values[0] = values[0].model_copy(
        update={
            "mean": values[0].mean.model_copy(update={"display": private}),
            "raw_comment": private,
        }
    )
    dto = dto.model_copy(
        update={"dimensions": tuple(values), "name": private, "internal_path": "D:/private"}
    )
    standard = rubric(report).model_copy(
        update={
            "criteria": tuple(
                c.model_copy(
                    update={
                        "criteria_text": private,
                        "display_name": private,
                        "original_name": private,
                    }
                )
                for c in rubric(report).criteria
            )
        }
    )
    normalized = from_statistics(
        dto, generated_at=report.generated_at, rubric=standard, group_pages=(pages(report),)
    )
    output = generate_report(normalized)
    assert "合成姓名" not in output.markdown
    assert "HYPERLINK" not in output.markdown
    assert "D:/private" not in output.markdown
    with ZipFile(BytesIO(output.xlsx)) as archive:
        xml = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xml")
        )
    for secret in ("合成姓名", "202600000001", "HYPERLINK", "D:/private"):
        assert secret not in xml


def test_inconsistent_numeric_hint_and_unknown_dimension_are_rejected():
    report = synthetic_report(4)
    dto = statistics(report)
    first = dto.dimensions[0]
    wrong = first.model_copy(update={"mean": first.mean.model_copy(update={"value": 1.0})})
    with pytest.raises(ReportError, match="numeric_hint_mismatch"):
        from_statistics(
            dto.model_copy(update={"dimensions": (wrong,)}), generated_at=report.generated_at
        )
    with pytest.raises(ReportError, match="invalid_dimension_mapping"):
        from_statistics(
            dto.model_copy(
                update={
                    "dimensions": (first.model_copy(update={"dimension_code": "unrecognized"}),)
                }
            ),
            generated_at=report.generated_at,
        )


def test_zero_match_complete_page_preserves_zero_not_missing():
    report = synthetic_report(17)
    empty = synthetic_report(0)
    group = replace(
        report.groups[0],
        conditions=(replace(report.groups[0].conditions[0], operator="gt", threshold=Fraction(5)),),
        matched_count=0,
        ratio=Fraction(0),
        eligible_ratio=Fraction(0),
        members=(),
        statistics=tuple(replace(d, source=report.source) for d in empty.dimensions),
    )
    result = adapt(replace(report, groups=(group,)))
    assert result.groups[0].members == ()
    assert result.groups[0].ratio == 0
    output = generate_report(result)
    assert "没有匹配记录" in output.markdown
    assert "0.00%" in output.markdown


def test_summary_only_does_not_implicitly_authorize_a_complete_group():
    report = synthetic_report(17)
    normalized = adapt(report, pages(report, include_statistics=False))
    assert normalized.groups[0].statistics is None
    with pytest.raises(ReportError, match="detail_authorization_required"):
        generate_report(normalized, include_details=True)
    # Renaming/mutating input and setting a flag cannot use the built-in demo grant helper.
    with pytest.raises(ValueError, match="synthetic_demo_only"):
        synthetic_authorization(replace(report, record_count=18))
