"""Entirely synthetic saved DTOs; never read teacher files, HTTP or a database."""

from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from io import BytesIO
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from app.analysis.edu import AssessmentRecord, Dimension, ScorePolicy, prepare_dataset
from app.analysis.edu import Condition as AnalysisCondition
from app.analysis.edu.insights import DiscoveryPolicy, GroupRule, discover_candidates
from app.edu.analysis import CODE_MAP, DIMENSION_MAP, OPERATOR_MAP, RULE_VERSION, stat
from app.edu.insight_models import InsightSpec
from app.edu.insights import card_view
from app.edu.models import Student, StudentScore
from app.edu.review_models import AnalysisResult, AnalysisSnapshot, FindingDecision, SavedFinding
from app.reporting.edu import ReportError, generate_report, with_reviewed_findings
from app.reporting.edu.demo import synthetic_report
from tests.reporting.test_adapters import statistics


def saved_inputs(size=17, *, statuses=None, filtered=True):
    """Run existing discovery, then wrap its real metrics in a synthetic saved result."""
    original = synthetic_report(size)
    report = replace(
        original,
        rule_version=RULE_VERSION,
        groups=(replace(original.groups[0], rule_version=RULE_VERSION),) if filtered else (),
    )
    patterns = ((2, 4), (Fraction(11, 4), 5), (3, 4), (4, 3))
    rows = tuple(
        AssessmentRecord(
            report.source.batch_id,
            f"synthetic-member-{i:04d}",
            f"synthetic-record-{i:04d}",
            "synthetic-source",
            {
                dim: patterns[i % 4][0]
                if dim is Dimension.ALGORITHMS
                else patterns[i % 4][1]
                if dim is Dimension.STRATEGY
                else 3
                for dim in Dimension
            },
        )
        for i in range(size)
    )
    data = prepare_dataset(rows, policy=ScorePolicy(step=Fraction(1, 100)))
    conditions = (
        tuple(
            {
                "dimension_code": c.dimension_code,
                "operator": c.operator,
                "value": Decimal(c.threshold.numerator) / Decimal(c.threshold.denominator),
            }
            for c in original.groups[0].conditions
        )
        if filtered
        else ()
    )
    spec = InsightSpec(snapshot_id=report.source.snapshot_id, conditions=conditions)
    groups = (
        (
            GroupRule(
                "edu.insight.custom_group.v1",
                tuple(
                    AnalysisCondition(
                        DIMENSION_MAP[c.dimension_code], OPERATOR_MAP[c.operator], Fraction(c.value)
                    )
                    for c in spec.conditions
                ),
            ),
        )
        if filtered
        else ()
    )
    cards = tuple(
        card_view(c, spec, report.source.batch_id)
        for c in discover_candidates(
            data,
            batch_id=report.source.batch_id,
            snapshot_id=report.source.snapshot_id,
            groups=groups,
            policy=DiscoveryPolicy(),
        )
    )
    matched = {row.student_id for row in original.groups[0].members} if filtered else None
    students = tuple(
        Student(
            student_id=row.reference.student_id,
            display_label="合成身份不得输出 202600000001",
            scores=tuple(
                StudentScore(
                    dimension_code=CODE_MAP[c.dimension],
                    score=float(c.value),
                    evidence_id=f"synthetic-evidence-{i}-{c.dimension.name.lower()}",
                )
                for c in row.scores
            ),
        )
        for i, row in enumerate(data.records)
        if matched is None or row.reference.student_id in matched
    )
    snapshot = AnalysisSnapshot(
        query=spec,
        statistics=statistics(report),
        group_statistics=statistics(
            report, original.groups[0].statistics, total=len(students), kind="matched_group"
        )
        if filtered
        else None,
        total_count=size,
        matched_count=len(students),
        ratio=stat(Fraction(len(students), size) if size else None),
        members=students,
        candidates=cards,
    )
    statuses = statuses or ("confirmed",) * len(cards)
    result = AnalysisResult(
        result_id="synthetic-saved-result",
        batch_id=report.source.batch_id,
        snapshot_id=report.source.snapshot_id,
        version=2,
        title="合成教师标题，不得直接导出",
        note="合成教师备注，不得直接导出",
        reviewed_by="synthetic-reviewer",
        created_at="2026-10-25T00:00:00Z",
        updated_at="2026-10-25T00:00:00Z",
        analysis=snapshot,
        findings=tuple(
            SavedFinding(
                candidate=card,
                teacher_review=FindingDecision(
                    candidate_id=card.candidate_id,
                    status=statuses[i],
                    note="合成逐卡备注，不得导出",
                ),
            )
            for i, card in enumerate(cards)
        ),
    )
    return report, result


def attach(report, result, **options):
    return with_reviewed_findings(
        report,
        result,
        expected_result_id="synthetic-saved-result",
        expected_version=options.get("version", 2),
    )


def mutate_card(result, change, index=0):
    """Alter both saved projections to test metrics beyond the membership check."""
    cards = list(result.analysis.candidates)
    findings = list(result.findings)
    card = change(cards[index])
    cards[index] = card
    findings[index] = findings[index].model_copy(update={"candidate": card})
    return result.model_copy(
        update={
            "analysis": result.analysis.model_copy(update={"candidates": tuple(cards)}),
            "findings": tuple(findings),
        }
    )


def change_metric(card, key, **updates):
    return card.model_copy(
        update={
            "metrics": tuple(
                m.model_copy(update=updates) if m.key == key else m for m in card.metrics
            )
        }
    )


def workbook_text(raw):
    workbook = load_workbook(BytesIO(raw))
    text = "\n".join(
        str(c.value) for ws in workbook for row in ws for c in row if c.value is not None
    )
    return workbook, text


@pytest.mark.parametrize("size", [1, 17, 53])
@pytest.mark.parametrize("filtered", [False, True])
def test_confirmed_metrics_and_versions_enter_both_formats(size, filtered):
    report, result = saved_inputs(size, filtered=filtered)
    projected = attach(report, result)
    files = generate_report(projected)
    workbook, text = workbook_text(files.xlsx)
    assert len(projected.reviewed.findings) == (6 if filtered else 5)
    assert "教师已确认" in text and "教师已确认" in files.markdown
    assert "教师核验版本" in text and "| 教师核验版本 | 2 |" in files.markdown
    assert "edu.report.reviewed.v1" in text and "edu.report.reviewed.v1" in files.markdown
    assert "洞察卡未提供" not in text + files.markdown
    assert "不证明因果关系" in text + files.markdown
    assert workbook.sheetnames == (
        ["测评概况", "维度统计"] + (["选定群体"] if filtered else []) + ["教师确认发现", "技术附录"]
    )
    for finding in projected.reviewed.findings:
        assert finding.candidate_id in text and finding.candidate_id in files.markdown
    if size == 17 and filtered:
        selected = next(
            f for f in projected.reviewed.findings if f.candidate_type == "ability_group"
        )
        metrics = {m.key: m for m in selected.metrics}
        assert metrics["matched_count"].value == 9
        assert metrics["matched_ratio"].value == Fraction(9, 17)
        assert metrics["eligible_count"].source_record_count == 17
        assert metrics["matched_ratio"].source_record_count == 9
        assert "9/17" in files.markdown and "52.94%" in files.markdown
        dimension = next(
            f for f in projected.reviewed.findings if f.dimension_code == "algorithm_data_structure"
        )
        assert next(m for m in dimension.metrics if m.key == "mean").value == Fraction(49, 17)
        assert "49/17" in files.markdown  # Hand check: 5*2 + 4*2.75 + 4*3 + 4*4 = 49.
    workbook.close()


@pytest.mark.parametrize("status", ["pending", "dismissed"])
def test_unconfirmed_are_not_exported_as_confirmed(status):
    report, result = saved_inputs(statuses=(status,) * 6)
    projected = attach(report, result)
    assert projected.reviewed.findings == ()
    files = generate_report(projected)
    workbook, text = workbook_text(files.xlsx)
    assert "教师确认发现" not in workbook.sheetnames
    assert "## 教师确认发现" not in files.markdown
    assert all(f.candidate.candidate_id not in text + files.markdown for f in result.findings)
    workbook.close()


def test_mixed_decisions_only_export_confirmed_and_no_insight_input_still_works():
    report, result = saved_inputs(statuses=("confirmed", "pending", "dismissed") * 2)
    projected = attach(report, result)
    assert len(projected.reviewed.findings) == 2
    old = generate_report(report)
    assert "洞察卡未提供" not in old.markdown
    assert "## 教师确认发现" not in old.markdown
    assert (
        "暂无教师已确认发现" not in old.markdown
    )  # Missing input != saved result with zero confirmed.
    assert projected.groups == report.groups and projected.dimensions == report.dimensions


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.model_copy(update={"batch_id": "synthetic-other-batch"}),
        lambda r: r.model_copy(update={"snapshot_id": "synthetic-other-snapshot"}),
        lambda r: r.model_copy(update={"version": 3}),
        lambda r: r.model_copy(update={"result_id": "synthetic-other-result"}),
        lambda r: r.model_copy(
            update={
                "analysis": r.analysis.model_copy(
                    update={
                        "query": r.analysis.query.model_copy(
                            update={"snapshot_id": "synthetic-other-snapshot"}
                        )
                    }
                )
            }
        ),
        lambda r: r.model_copy(
            update={"analysis": r.analysis.model_copy(update={"matched_count": 8})}
        ),
        lambda r: r.model_copy(
            update={"analysis": r.analysis.model_copy(update={"total_count": 105})}
        ),
        lambda r: r.model_copy(
            update={"analysis": r.analysis.model_copy(update={"ratio": stat(Fraction(1))})}
        ),
        lambda r: r.model_copy(
            update={"analysis": r.analysis.model_copy(update={"members": r.analysis.members[:-1]})}
        ),
        lambda r: r.model_copy(
            update={
                "analysis": r.analysis.model_copy(
                    update={
                        "statistics": r.analysis.statistics.model_copy(
                            update={"rule_version": "conflicting-version"}
                        )
                    }
                )
            }
        ),
        lambda r: mutate_card(
            r, lambda c: c.model_copy(update={"rule_version": "conflicting-version"})
        ),
        lambda r: mutate_card(r, lambda c: c.model_copy(update={"denominator": 105})),
        lambda r: mutate_card(r, lambda c: c.model_copy(update={"rule_id": "unknown-rule"})),
        lambda r: mutate_card(
            r, lambda c: c.model_copy(update={"snapshot_id": "synthetic-other-snapshot"})
        ),
        lambda r: mutate_card(
            r, lambda c: change_metric(c, "matched_count", value=stat(Fraction(8)))
        ),
        lambda r: mutate_card(r, lambda c: change_metric(c, "matched_ratio", denominator=9)),
        lambda r: mutate_card(
            r, lambda c: change_metric(c, "eligible_count", source_record_count=9)
        ),
        lambda r: mutate_card(
            r, lambda c: change_metric(c, "matched_ratio", source_record_count=17)
        ),
    ],
)
def test_mismatched_saved_metadata_or_metrics_are_rejected(mutation):
    report, result = saved_inputs()
    with pytest.raises(ReportError):
        attach(report, mutation(result))


def test_candidate_must_exist_in_saved_result_and_decision_must_match():
    report, result = saved_inputs()
    first = result.findings[0]
    unknown = first.candidate.model_copy(update={"candidate_id": "candidate-not-saved"})
    invalid = first.model_copy(update={"candidate": unknown})
    with pytest.raises(ReportError, match="candidate_not_in_saved_result"):
        attach(report, result.model_copy(update={"findings": (invalid, *result.findings[1:])}))
    invalid = first.model_copy(
        update={
            "teacher_review": first.teacher_review.model_copy(
                update={"candidate_id": "candidate-not-saved"}
            )
        }
    )
    with pytest.raises(ReportError, match="saved_decision_mismatch"):
        attach(report, result.model_copy(update={"findings": (invalid, *result.findings[1:])}))


def test_changed_analysis_conditions_or_dimension_options_are_rejected():
    report, result = saved_inputs()
    conditions = list(result.analysis.query.conditions)
    conditions[0] = conditions[0].model_copy(update={"operator": "lte"})
    query = result.analysis.query.model_copy(update={"conditions": tuple(conditions)})
    with pytest.raises(ReportError, match="reviewed_conditions_mismatch"):
        attach(
            report,
            result.model_copy(
                update={"analysis": result.analysis.model_copy(update={"query": query})}
            ),
        )
    query = result.analysis.query.model_copy(update={"include_dimensions": False})
    with pytest.raises(ReportError, match="candidate_not_in_saved_query"):
        attach(
            report,
            result.model_copy(
                update={"analysis": result.analysis.model_copy(update={"query": query})}
            ),
        )


def test_free_text_and_personal_fields_never_cross_projection():
    report, result = saved_inputs()
    payload = (
        '=HYPERLINK("https://example.invalid","合成姓名 202600000001 withheld sk-not-a-secret")'
    )
    for i in range(6):
        result = mutate_card(
            result,
            lambda c: c.model_copy(
                update={
                    "title": payload,
                    "summary": payload,
                    "limitations": (payload,),
                    "suggested_next_action": payload,
                    "public_restrictions": (payload,),
                }
            ),
            i,
        )
    result = result.model_copy(
        update={
            "title": payload,
            "note": payload,
            "reviewed_by": payload,
            "notice": payload,
            "created_at": payload,
            "updated_at": payload,
        }
    )
    result = result.model_copy(
        update={
            "findings": tuple(
                f.model_copy(
                    update={"teacher_review": f.teacher_review.model_copy(update={"note": payload})}
                )
                for f in result.findings
            )
        }
    )
    files = generate_report(attach(report, result))
    workbook, text = workbook_text(files.xlsx)
    with ZipFile(BytesIO(files.xlsx)) as archive:
        xml = "\n".join(
            archive.read(name).decode() for name in archive.namelist() if name.endswith(".xml")
        )
        assert "externalLinks" not in " ".join(archive.namelist())
    for forbidden in (
        "HYPERLINK",
        "example.invalid",
        "合成姓名",
        "202600000001",
        "sk-not-a-secret",
        "synthetic-reviewer",
        "synthetic-member-",
        "synthetic-evidence-",
    ):
        assert forbidden not in text + files.markdown + xml
    assert all(
        c.data_type != "f" and c.hyperlink is None for ws in workbook for row in ws for c in row
    )
    workbook.close()


def test_dict_boolean_and_forged_internal_values_are_not_confirmation():
    report, result = saved_inputs()
    with pytest.raises(ReportError, match="saved_analysis_result_required"):
        attach(report, result.model_dump())
    with pytest.raises(ReportError, match="saved_analysis_result_required"):
        attach(report, True)
    with pytest.raises(ReportError, match="saved_result_version_mismatch"):
        attach(report, result, version=True)
    projected = attach(report, result)
    first = projected.reviewed.findings[0]
    invalid = replace(
        first, metrics=(replace(first.metrics[0], source_record_count=105), *first.metrics[1:])
    )
    with pytest.raises(ReportError, match="finding_metric_mismatch"):
        generate_report(
            replace(
                projected,
                reviewed=replace(
                    projected.reviewed, findings=(invalid, *projected.reviewed.findings[1:])
                ),
            )
        )


def test_stable_order_and_zero_candidates_do_not_invent_findings():
    report, result = saved_inputs()
    reversed_result = result.model_copy(
        update={
            "findings": tuple(reversed(result.findings)),
            "analysis": result.analysis.model_copy(
                update={
                    "candidates": tuple(reversed(result.analysis.candidates)),
                    "members": tuple(reversed(result.analysis.members)),
                }
            ),
        }
    )
    assert generate_report(attach(report, result)) == generate_report(
        attach(report, reversed_result)
    )
    report, result = saved_inputs(0)
    assert "## 教师确认发现" not in generate_report(attach(report, result)).markdown


@pytest.mark.parametrize(
    "key,updates",
    [
        ("mean", {"value": stat(Fraction(3))}),
        ("median", {"denominator": 9}),
        ("mean", {"source_record_count": 9}),
        ("frequency:2", {"source_record_count": 17}),
        ("frequency:2", {"at_score": stat(Fraction(11, 4))}),
        ("valid_count", {"unit": "score"}),
    ],
)
def test_dimension_metric_semantics_are_checked_individually(key, updates):
    report, result = saved_inputs()
    i = next(
        i
        for i, c in enumerate(result.analysis.candidates)
        if c.dimension_code == "algorithm_data_structure"
    )
    invalid = mutate_card(result, lambda c: change_metric(c, key, **updates), i)
    with pytest.raises(ReportError, match="finding_metric_mismatch"):
        attach(report, invalid)


def test_saved_member_evidence_and_group_statistics_cannot_drift():
    report, result = saved_inputs()
    member = result.analysis.members[0]
    altered = member.model_copy(
        update={
            "scores": (
                member.scores[0].model_copy(update={"evidence_id": "synthetic-other-evidence"}),
                *member.scores[1:],
            )
        }
    )
    snapshot = result.analysis.model_copy(
        update={"members": (altered, *result.analysis.members[1:])}
    )
    with pytest.raises(ReportError, match="saved_member_source_mismatch"):
        attach(report, result.model_copy(update={"analysis": snapshot}))
    snapshot = result.analysis.model_copy(
        update={
            "group_statistics": result.analysis.group_statistics.model_copy(
                update={"snapshot_id": "synthetic-other"}
            )
        }
    )
    with pytest.raises(ReportError, match="statistics_scope_mismatch"):
        attach(report, result.model_copy(update={"analysis": snapshot}))


def test_condition_order_is_semantic_but_candidate_conditions_are_not_free():
    report, result = saved_inputs()
    reversed_query = result.analysis.query.model_copy(
        update={"conditions": tuple(reversed(result.analysis.query.conditions))}
    )
    assert (
        attach(
            report,
            result.model_copy(
                update={"analysis": result.analysis.model_copy(update={"query": reversed_query})}
            ),
        ).reviewed
        == attach(report, result).reviewed
    )
    invalid = mutate_card(result, lambda c: c.model_copy(update={"conditions": ()}))
    with pytest.raises(ReportError, match="candidate_conditions_mismatch"):
        attach(report, invalid)
