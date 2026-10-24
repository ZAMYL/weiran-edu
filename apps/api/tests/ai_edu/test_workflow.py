from fractions import Fraction

import pytest

from app.ai.edu import SyntheticInput
from app.ai.edu.models import ToolCall
from app.ai.edu.tools import encoded
from app.analysis.edu import AssessmentRecord, Dimension, ScorePolicy, prepare_dataset

from .conftest import FakeLLM, call


def test_exact_statistics_and_provenance(scenario):
    result = scenario.executor().execute(ToolCall(**call()))
    assert result.status == "ok"
    mean = next(
        item
        for item in result.facts
        if item.dimension is Dimension.ALGORITHMS and item.metric == "mean"
    )
    # [2, 2.5, 3, 4]: mean=23/8, median=11/4, n=4.
    assert (mean.value, mean.denominator, mean.scope.student_count) == (Fraction(23, 8), 4, 4)
    assert next(
        item.value
        for item in result.facts
        if item.dimension is Dimension.ALGORITHMS and item.metric == "median"
    ) == Fraction(11, 4)
    assert mean.source_refs == mean.scope.records
    assert mean.batch_id == "synthetic-batch"
    assert mean.rule_id == "edu.mean" and "draft" in mean.rule_version
    assert mean.model_view()["exact_value"] == {"numerator": 23, "denominator": 8}
    assert {ref.batch_id for ref in mean.source_refs} == {"synthetic-batch"}


def test_complete_flow_repeatable_result_and_trace(scenario):
    calls = [
        call(),
        call(
            "filter_student_group",
            conditions=[
                {"dimension": "ALGORITHMS", "operator": "<", "threshold": "3"},
                {"dimension": "STRATEGY", "operator": ">=", "threshold": "4"},
            ],
        ),
        call("get_evidence_records", evidence_ids=["score-alpha", "comment-alpha"]),
        call("get_rubric_criteria"),
    ]
    first = scenario.run(FakeLLM(calls=calls))
    assert first == scenario.run(FakeLLM(calls=calls))
    assert first.status == "validated" and first.error_code is None
    assert len(first.trace) == 4
    report = first.report
    assert report.observed_facts[0].text.startswith("算法与数据结构：mean=23/8")
    assert report.hypotheses[0].status == "hypothesis_unverified"
    assert report.teaching_actions[0].cited_fact_ids == report.cited_fact_ids
    assert all(step.status == "ok" for step in first.trace)
    assert any("人工复核" in text for text in report.limitations)


def test_and_filter_denominators_reuse_engine(scenario):
    conditions = [
        {"dimension": "STRATEGY", "operator": ">=", "threshold": "4"},
        {"dimension": "ALGORITHMS", "operator": "<", "threshold": "3"},
        {"dimension": "EFFICIENCY", "operator": "<=", "threshold": "2"},
    ]
    result = scenario.executor().execute(
        ToolCall(**call("filter_student_group", conditions=conditions))
    )
    facts = {item.metric: item for item in result.facts}
    # First two students satisfy all three conditions: 2/4 = 1/2.
    assert facts["matched_count"].value == 2
    assert facts["matched_ratio"].value == Fraction(1, 2)
    assert facts["matched_ratio"].denominator == 4
    assert len(facts["matched_count"].source_refs) == 2
    assert facts["matched_count"].conditions[1].operator.value == "<"
    assert facts["matched_count"].scope.student_count == 4
    assert (
        scenario.run(FakeLLM(calls=[call("filter_student_group", conditions=conditions)])).status
        == "validated"
    )


@pytest.mark.parametrize("operator,expected", [("<", 2), ("<=", 3)])
def test_strict_less_boundary(scenario, operator, expected):
    result = scenario.executor().execute(
        ToolCall(
            **call(
                "filter_student_group",
                conditions=[{"dimension": "ALGORITHMS", "operator": operator, "threshold": "3"}],
            )
        )
    )
    assert result.facts[0].value == expected


def test_provider_documents_read_and_bound_not_sent_to_model(scenario):
    executor = scenario.executor()
    evidence = executor.execute(
        ToolCall(
            **call(
                "get_evidence_records",
                evidence_ids=["comment-alpha", "score-alpha", "score-alpha"],
            )
        )
    )
    rubric = executor.execute(ToolCall(**call("get_rubric_criteria")))
    assert len(evidence.documents) == 2 and len(rubric.documents) == 5
    assert evidence.documents[0].text.startswith("合成评语")
    assert all(item.grant.version == "synthetic-rubric-draft" for item in rubric.documents)
    assert len(scenario.evidence.reads) == 2 and len(scenario.rubric.reads) == 5
    assert "合成评语" not in encoded(evidence.model_view()).decode()
    assert rubric.model_view()["evidence_metadata"][0]["rubric_version"] == "synthetic-rubric-draft"
    assert set(executor.documents) == executor.evidence_ids


def test_missing_invalid_never_zero(scenario):
    dataset = prepare_dataset(
        [
            AssessmentRecord(
                "synthetic-batch",
                f"synth-{index}",
                f"synth-row-{index}",
                "synth-source",
                {Dimension.ALGORITHMS: value},
            )
            for index, value in enumerate([2.5, None, "invalid"])
        ],
        policy=ScorePolicy(),
    )
    access = scenario.source.access.model_copy(
        update={
            "student_ids": tuple(f"synth-{index}" for index in range(3)),
            "evidence_grants": (),
            "rubric_grants": (),
        }
    )
    scenario.source = SyntheticInput(dataset, access)
    outcome = scenario.run()
    assert outcome.status == "validated"
    facts = {item.metric: item for item in outcome.facts if item.dimension is Dimension.ALGORITHMS}
    assert facts["mean"].value == Fraction(5, 2) and facts["mean"].denominator == 1
    assert (facts["mean"].missing_count, facts["mean"].invalid_count) == (1, 1)
    assert any("缺失" in text for text in outcome.report.limitations)


@pytest.mark.parametrize("empty", [True, False])
def test_insufficient_fallback_without_compose(scenario, empty):
    dataset = prepare_dataset(
        []
        if empty
        else [
            AssessmentRecord(
                "synthetic-batch",
                "synthetic-no-score",
                "synthetic-row",
                "synthetic-source",
                {},
            )
        ],
        policy=ScorePolicy(),
    )
    access = scenario.source.access.model_copy(
        update={
            "student_ids": () if empty else ("synthetic-no-score",),
            "evidence_grants": (),
            "rubric_grants": (),
        }
    )
    scenario.source = SyntheticInput(dataset, access)
    client = FakeLLM()
    outcome = scenario.run(client)
    assert outcome.status == "insufficient_data" and client.compose_count == 0
    assert not outcome.report.hypotheses and not outcome.report.teaching_actions
    assert any("尚不能" in text for text in outcome.report.limitations)
    assert all(item.value is None for item in outcome.facts if item.metric in {"mean", "median"})


def test_explicit_subgroup_and_empty_subgroup(scenario):
    result = scenario.executor().execute(ToolCall(**call(student_ids=["synthetic-student-0"])))
    assert next(item for item in result.facts if item.metric == "mean").value == 2
    assert all(item.scope.student_count == 1 for item in result.facts)
    empty = scenario.executor().execute(ToolCall(**call(student_ids=[])))
    assert all(item.denominator == 0 for item in empty.facts)


def test_private_records_not_in_prompts_trace_or_logs(scenario, caplog, capsys):
    client = FakeLLM(
        calls=[
            call(),
            call("get_evidence_records", evidence_ids=["comment-alpha"]),
            call("get_rubric_criteria"),
        ]
    )
    result = scenario.run(client)
    assert result.status == "validated"
    prompts = "\n".join(client.prompts)
    for marker in (
        "synthetic-student",
        "synthetic-row",
        "synthetic-source",
        "合成评语",
        "合成评价依据",
    ):
        assert (
            marker not in prompts and marker not in repr(result.trace) and marker not in caplog.text
        )
    captured = capsys.readouterr()
    assert not captured.out and not captured.err


def test_unread_evidence_and_rubric_are_declared(scenario):
    report = scenario.run().report
    assert not report.cited_evidence_ids
    assert any("未取得" in text for text in report.limitations)
    assert any("未读取" in text for text in report.limitations)
