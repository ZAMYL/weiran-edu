"""Synthetic regression cases for contributing-record integrity, not semantic truth."""

import json
from dataclasses import replace
from fractions import Fraction

import pytest

from app.ai.edu import EvidenceDocument, EvidenceGrant, SyntheticAccess, SyntheticInput, ToolCall
from app.ai.edu.models import InsightError, Proposal
from app.ai.edu.validation import validate_proposal
from app.analysis.edu import (
    AssessmentRecord,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    ScorePolicy,
    prepare_dataset,
)

from .conftest import FakeLLM, MemoryProvider, call


@pytest.fixture
def source_scenario(scenario):
    rows = []
    for index, score in enumerate((2, 3, 4, None, "synthetic-invalid")):
        refs = tuple(
            EvidenceRef("synthetic-source", f"synthetic-row-{index}/{label}", kind, dimension)
            for label, kind, dimension in (
                ("score", EvidenceKind.SCORE, Dimension.ALGORITHMS),
                ("comment", EvidenceKind.COMMENT, Dimension.ALGORITHMS),
                ("general", EvidenceKind.COMMENT, None),
            )
        )
        scores = {dimension: 3 for dimension in Dimension}
        scores[Dimension.ALGORITHMS] = score
        rows.append(
            AssessmentRecord(
                "synthetic-batch",
                f"synthetic-member-{index}",
                f"synthetic-row-{index}",
                "synthetic-source",
                scores,
                refs,
            )
        )
    dataset = prepare_dataset(rows, policy=ScorePolicy())
    grants = tuple(
        EvidenceGrant(
            evidence_id=f"synthetic-{index}-{label}",
            record=record.reference,
            reference=reference,
        )
        for index, record in enumerate(dataset.records)
        for label, reference in zip(
            ("score", "comment", "general"),
            record.reference.evidence_refs,
            strict=True,
        )
    )
    access = SyntheticAccess(
        batch_id="synthetic-batch",
        student_ids=tuple(record.reference.student_id for record in dataset.records),
        evidence_grants=grants,
        rubric_grants=scenario.source.access.rubric_grants,
    )
    scenario.source = SyntheticInput(dataset, access)
    scenario.evidence = MemoryProvider(
        {
            grant.evidence_id: EvidenceDocument(grant, "合成证据文本；仅用于来源关联测试。")
            for grant in grants
        }
    )
    return scenario


def proposal_with_sources(prompt, metrics, evidence_ids):
    selected = [
        item["fact_id"]
        for item in json.loads(prompt)["facts"]
        if item["dimension"] in {"ALGORITHMS", None} and item["metric"] in metrics
    ]
    assert selected
    return {
        "observed_facts": [{"fact_id": identifier} for identifier in selected],
        "hypotheses": [
            {
                "kind": "practice_opportunity",
                "status": "hypothesis",
                "fact_ids": selected,
                "evidence_ids": evidence_ids,
            }
        ],
        "teaching_actions": [
            {
                "kind": "guided_practice",
                "fact_ids": selected,
                "evidence_ids": evidence_ids,
            }
        ],
        "verification_steps": [
            {
                "kind": "collect_work_samples",
                "fact_ids": selected,
                "evidence_ids": evidence_ids,
            }
        ],
        "limitations": [],
        "cited_fact_ids": selected,
        "cited_evidence_ids": evidence_ids,
    }


def run_with_sources(scenario, calls, metrics, evidence_ids, *, rubric=False):
    calls = [
        *calls,
        call("get_rubric_criteria")
        if rubric
        else call("get_evidence_records", evidence_ids=evidence_ids),
    ]
    return scenario.run(
        FakeLLM(
            calls=calls,
            response=lambda prompt: proposal_with_sources(prompt, metrics, evidence_ids),
        )
    )


def assert_link_result(outcome, allowed):
    if allowed:
        assert outcome.status == "validated" and outcome.error_code is None
        assert outcome.report is not None
    else:
        assert outcome.status == "error" and outcome.report is None
        assert outcome.error_code == "evidence_fact_scope_mismatch"


def filter_call(threshold="3", operator="<"):
    return call(
        "filter_student_group",
        conditions=[
            {"dimension": "ALGORITHMS", "operator": operator, "threshold": threshold},
        ],
    )


@pytest.mark.parametrize("kind", ["score", "comment"])
@pytest.mark.parametrize("index,allowed", [(0, True), (1, False), (3, False), (4, False)])
def test_only_matched_students_can_support_matched_facts(source_scenario, kind, index, allowed):
    outcome = run_with_sources(
        source_scenario,
        [filter_call()],
        {"matched_count", "matched_ratio"},
        [f"synthetic-{index}-{kind}"],
    )
    # Only score 2 satisfies <3; same-cohort 3, missing and invalid records do not.
    assert_link_result(outcome, allowed)


@pytest.mark.parametrize("kind", ["score", "comment", "general"])
@pytest.mark.parametrize("index,allowed", [(0, True), (1, True), (3, False), (4, False)])
def test_mean_sources_exclude_missing_and_invalid(source_scenario, kind, index, allowed):
    outcome = run_with_sources(
        source_scenario,
        [call()],
        {"mean"},
        [f"synthetic-{index}-{kind}"],
    )
    assert_link_result(outcome, allowed)


@pytest.mark.parametrize("kind", ["score", "comment"])
@pytest.mark.parametrize("index,allowed", [(0, True), (2, False), (3, False), (4, False)])
def test_distribution_requires_exact_bin_contributor(source_scenario, kind, index, allowed):
    outcome = run_with_sources(
        source_scenario,
        [call()],
        {"mean", "frequency_at_2"},
        [f"synthetic-{index}-{kind}"],
    )
    # The valid score 4 contributes to the denominator, not the score-2 frequency.
    assert_link_result(outcome, allowed)


@pytest.mark.parametrize(
    "metric,index,allowed",
    [
        ("minimum", 0, True),
        ("minimum", 2, False),
        ("maximum", 2, True),
        ("maximum", 0, False),
    ],
)
def test_extremum_evidence_identifies_the_extremum(source_scenario, metric, index, allowed):
    outcome = run_with_sources(
        source_scenario,
        [call()],
        {metric},
        [f"synthetic-{index}-score"],
    )
    assert_link_result(outcome, allowed)


@pytest.mark.parametrize("kind", ["score", "comment", "general"])
def test_zero_matches_has_no_personal_direct_evidence(source_scenario, kind):
    outcome = run_with_sources(
        source_scenario,
        [filter_call("1")],
        {"matched_ratio"},
        [f"synthetic-0-{kind}"],
    )
    assert_link_result(outcome, False)


@pytest.mark.parametrize("threshold,allowed", [("3", True), ("1", False)])
def test_one_document_must_match_every_fact_it_is_attached_to(source_scenario, threshold, allowed):
    outcome = run_with_sources(
        source_scenario,
        [filter_call("3"), filter_call(threshold)],
        {"matched_ratio"},
        ["synthetic-0-comment"],
    )
    assert_link_result(outcome, allowed)


@pytest.mark.parametrize(
    "metric,indices",
    [
        ("valid_count", (0, 1, 2)),
        ("missing_count", (3,)),
        ("invalid_count", (4,)),
        ("mean", (0, 1, 2)),
        ("median", (0, 1, 2)),
        ("minimum", (0,)),
        ("maximum", (2,)),
        ("frequency_at_2", (0,)),
        ("frequency_at_3", (1,)),
        ("frequency_at_4", (2,)),
    ],
)
def test_statistics_source_projection_matches_metric_not_denominator(
    source_scenario, metric, indices
):
    result = source_scenario.executor().execute(ToolCall(**call()))
    fact = next(
        item
        for item in result.facts
        if item.dimension is Dimension.ALGORITHMS and item.metric == metric
    )
    assert fact.source_refs == tuple(
        source_scenario.source.dataset.records[index].reference for index in indices
    )
    assert fact.scope.student_count == 5
    if metric == "mean":
        assert fact.value == 3 and fact.denominator == 3
    if metric.startswith("frequency_at_"):
        assert fact.value == 1 and fact.denominator == 3


@pytest.mark.parametrize(
    "metric,indices,value,denominator",
    [
        ("matched_count", (0,), 1, 5),
        ("matched_ratio", (0,), Fraction(1, 5), 5),
        ("matched_ratio_eligible", (0,), Fraction(1, 3), 3),
        ("eligible_count", (0, 1, 2), 3, 5),
        ("unevaluable_count", (3, 4), 2, 5),
    ],
)
def test_filter_source_projection_distinguishes_matched_eligible_unknown(
    source_scenario,
    metric,
    indices,
    value,
    denominator,
):
    result = source_scenario.executor().execute(ToolCall(**filter_call()))
    fact = next(item for item in result.facts if item.metric == metric)
    assert (fact.value, fact.denominator) == (value, denominator)
    assert fact.source_refs == tuple(
        source_scenario.source.dataset.records[index].reference for index in indices
    )
    assert fact.scope.student_count == 5


@pytest.mark.parametrize(
    "rubric_id,allowed",
    [
        ("rubric-ALGORITHMS", True),
        ("rubric-IMPLEMENTATION", False),
    ],
)
def test_rubric_uses_dimension_and_batch_not_student_membership(
    source_scenario, rubric_id, allowed
):
    outcome = run_with_sources(
        source_scenario,
        [filter_call("1")],
        {"matched_ratio"},
        [rubric_id],
        rubric=True,
    )
    # Zero matched records still have a batch criterion, never an individual example.
    assert_link_result(outcome, allowed)


def test_zero_match_fact_can_be_observed_without_personal_evidence(source_scenario):
    outcome = source_scenario.run(
        FakeLLM(
            calls=[filter_call("1")],
            response=lambda prompt: proposal_with_sources(prompt, {"matched_ratio"}, []),
        )
    )
    assert_link_result(outcome, True)
    fact = next(item for item in outcome.facts if item.metric == "matched_ratio")
    assert fact.value == 0 and fact.denominator == 5 and fact.source_refs == ()


def test_rubric_batch_guard_remains_explicit_in_validator(source_scenario):
    executor = source_scenario.executor()
    executor.execute(ToolCall(**filter_call("1")))
    executor.execute(ToolCall(**call("get_rubric_criteria")))
    view = json.dumps({"facts": [fact.model_view() for fact in executor.facts.values()]})
    proposal = Proposal.model_validate_json(
        json.dumps(
            proposal_with_sources(view, {"matched_ratio"}, ["rubric-ALGORITHMS"]),
        )
    )
    document = executor.documents["rubric-ALGORITHMS"]
    documents = {
        **executor.documents,
        "rubric-ALGORITHMS": replace(
            document,
            grant=document.grant.model_copy(update={"batch_id": "synthetic-other-batch"}),
        ),
    }
    with pytest.raises(InsightError, match="evidence_fact_scope_mismatch"):
        validate_proposal(
            proposal,
            executor.facts,
            executor.evidence_ids,
            executor.rubric_ids,
            documents,
        )


@pytest.mark.parametrize(
    "metric,index,allowed",
    [
        ("valid_count", 0, True),
        ("valid_count", 3, False),
        ("missing_count", 3, True),
        ("missing_count", 0, False),
        ("invalid_count", 4, True),
        ("invalid_count", 0, False),
        ("eligible_count", 1, True),
        ("eligible_count", 3, False),
        ("unevaluable_count", 4, True),
        ("unevaluable_count", 0, False),
    ],
)
def test_count_evidence_refers_to_the_counted_subset(source_scenario, metric, index, allowed):
    def response(prompt):
        facts = json.loads(prompt)["facts"]
        selected = [
            fact["fact_id"]
            for fact in facts
            if (fact["metric"] == "mean" and fact["dimension"] == "IMPLEMENTATION")
            or (fact["metric"] == metric and fact["dimension"] in {"ALGORITHMS", None})
        ]
        value = proposal_with_sources(prompt, {"mean"}, [f"synthetic-{index}-general"])
        value["observed_facts"] = [{"fact_id": identifier} for identifier in selected]
        for section in ("hypotheses", "teaching_actions", "verification_steps"):
            value[section][0]["fact_ids"] = selected
        value["cited_fact_ids"] = selected
        return value

    outcome = source_scenario.run(
        FakeLLM(
            calls=[
                call(),
                filter_call(),
                call("get_evidence_records", evidence_ids=[f"synthetic-{index}-general"]),
            ],
            response=response,
        )
    )
    # An unscoped comment can accompany a record; membership must still match EACH fact.
    assert_link_result(outcome, allowed)


@pytest.mark.parametrize(
    "metric,second_score,indices",
    [
        ("minimum", 2, (0, 1)),
        ("maximum", 4, (1, 2)),
        ("frequency_at_2", 2, (0, 1)),
    ],
)
def test_tied_values_keep_all_actual_contributors(source_scenario, metric, second_score, indices):
    rows = [
        AssessmentRecord(
            item.reference.batch_id,
            item.reference.student_id,
            item.reference.record_id,
            item.reference.source_id,
            {
                score.dimension: second_score
                if index == 1 and score.dimension is Dimension.ALGORITHMS
                else score.raw_value
                for score in item.scores
            },
            item.reference.evidence_refs,
        )
        for index, item in enumerate(source_scenario.source.dataset.records)
    ]
    dataset = prepare_dataset(rows, policy=ScorePolicy())
    source_scenario.source = SyntheticInput(dataset, source_scenario.source.access)
    result = source_scenario.executor().execute(ToolCall(**call()))
    fact = next(
        item
        for item in result.facts
        if item.dimension is Dimension.ALGORITHMS and item.metric == metric
    )
    assert fact.source_refs == tuple(dataset.records[index].reference for index in indices)
    outcome = run_with_sources(
        source_scenario,
        [call()],
        {"mean", metric},
        ["synthetic-1-score"],
    )
    assert_link_result(outcome, True)
