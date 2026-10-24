from dataclasses import replace

import pytest

from app.ai.edu import Limits, SyntheticInput, ToolCall
from app.ai.edu.models import InsightError
from app.analysis.edu import Dimension

from .conftest import FakeLLM, call, proposal


@pytest.mark.parametrize("phase", ["evidence", "model"])
def test_internal_exception_type_still_cannot_leak_arbitrary_text(scenario, monkeypatch, phase):
    if phase == "evidence":

        def fail(*args):
            raise InsightError("SYNTHETIC_SECRET_DO_NOT_ECHO")

        monkeypatch.setattr(scenario.evidence, "get", fail)
        client = FakeLLM(
            calls=[
                call(),
                call("get_evidence_records", evidence_ids=["comment-alpha"]),
            ]
        )
    else:

        class PrivateFailure(FakeLLM):
            async def plan(self, prompt):
                raise InsightError("SYNTHETIC_SECRET_DO_NOT_ECHO")

        client = PrivateFailure()
    outcome = scenario.run(client)
    assert outcome.status == "error" and outcome.report is None
    assert "SECRET" not in repr(outcome) and "SECRET" not in repr(outcome.trace)


@pytest.mark.parametrize("mismatch", ["group", "dimension"])
def test_authorized_but_unrelated_evidence_cannot_support_a_fact(scenario, mismatch):
    def response(prompt):
        result = proposal(prompt)
        result["hypotheses"][0]["evidence_ids"] = ["comment-alpha"]
        result["cited_evidence_ids"] = ["comment-alpha"]
        if mismatch == "dimension":
            # Select its ID by schema data, never hard-code an unverified result.
            import json

            facts = json.loads(prompt)["facts"]
            identifier = next(
                item["fact_id"]
                for item in facts
                if item["dimension"] == "IMPLEMENTATION" and item["metric"] == "mean"
            )
            result["observed_facts"] = [{"fact_id": identifier}]
            for key in ("hypotheses", "teaching_actions", "verification_steps"):
                result[key][0]["fact_ids"] = [identifier]
            result["cited_fact_ids"] = [identifier]
        return result

    stats = call(student_ids=["synthetic-student-1"]) if mismatch == "group" else call()
    outcome = scenario.run(
        FakeLLM(
            calls=[
                stats,
                call("get_evidence_records", evidence_ids=["comment-alpha"]),
            ],
            response=response,
        )
    )
    assert outcome.error_code == "evidence_fact_scope_mismatch" and outcome.report is None


def test_null_or_count_only_basis_does_not_allow_teaching_explanation(scenario):
    def response(prompt):
        import json

        value = proposal(prompt)
        count_id = next(
            item["fact_id"]
            for item in json.loads(prompt)["facts"]
            if item["metric"] == "valid_count"
        )
        value["observed_facts"] = [{"fact_id": count_id}]
        for key in ("hypotheses", "teaching_actions", "verification_steps"):
            value[key][0]["fact_ids"] = [count_id]
        value["cited_fact_ids"] = [count_id]
        return value

    outcome = scenario.run(FakeLLM(response=response))
    assert outcome.error_code == "unsupported_fact_basis"


def test_tool_raw_content_is_included_in_byte_budget(scenario):
    document = scenario.evidence.documents["comment-alpha"]
    scenario.evidence.documents["comment-alpha"] = replace(document, text="合成" * 1320)
    executor = scenario.executor(limits=Limits(max_tool_bytes=8000))
    result = executor.execute(
        ToolCall(
            **call(
                "get_evidence_records",
                evidence_ids=["comment-alpha"],
            )
        )
    )
    assert result.error_code == "tool_output_limit"
    assert not executor.documents and not executor.evidence_ids


def test_cumulative_limit_after_a_success_does_not_erase_or_publish_extra_facts(scenario):
    baseline = scenario.executor()
    baseline.execute(ToolCall(**call()))
    size = baseline.trace[0].output_bytes
    executor = scenario.executor(limits=Limits(max_total_tool_bytes=size + 1))
    assert executor.execute(ToolCall(**call())).status == "ok"
    original = dict(executor.facts)
    assert executor.execute(ToolCall(**call())).error_code == "tool_output_limit"
    assert executor.facts == original


def test_model_prompt_limit_prevents_even_planning(scenario):
    client = FakeLLM()
    result = scenario.run(client, limits=Limits(max_total_tool_bytes=256))
    assert result.error_code == "prompt_size_limit" and client.prompts == []


@pytest.mark.parametrize(
    "change,code",
    [
        (
            {"student_ids": ("synthetic-student-0", "synthetic-student-0")},
            "invalid_authorization_scope",
        ),
        ({"evidence_grants": "duplicate"}, "ambiguous_evidence_grant"),
        ({"rubric_grants": "wrong_batch"}, "invalid_rubric_grant"),
    ],
)
def test_trusted_snapshot_invariants_are_checked(scenario, change, code):
    change = dict(change)
    if change.get("evidence_grants") == "duplicate":
        grant = scenario.source.access.evidence_grants[0]
        change["evidence_grants"] = (grant, grant)
    if change.get("rubric_grants") == "wrong_batch":
        grant = scenario.source.access.rubric_grants[0]
        change["rubric_grants"] = (grant.model_copy(update={"batch_id": "synthetic-other-batch"}),)
    scenario.source = SyntheticInput(
        scenario.source.dataset,
        scenario.source.access.model_copy(update=change),
    )
    assert scenario.run().error_code == code


def test_partial_rubric_is_not_silently_called_complete(scenario):
    scenario.source = SyntheticInput(
        scenario.source.dataset,
        scenario.source.access.model_copy(
            update={
                "rubric_grants": scenario.source.access.rubric_grants[:1],
            }
        ),
    )
    outcome = scenario.run(FakeLLM(calls=[call(), call("get_rubric_criteria")]))
    assert outcome.error_code == "rubric_incomplete" and outcome.report is None


def test_missing_or_withheld_content_does_not_turn_into_normal_report(scenario):
    scenario.evidence.documents.clear()
    result = scenario.run(
        FakeLLM(
            calls=[
                call(),
                call("get_evidence_records", evidence_ids=["comment-alpha"]),
            ]
        )
    )
    assert result.status == "error" and result.error_code == "evidence_missing"
    assert result.report is None


def test_fractional_threshold_is_canonical_and_aggregate_scope_isolated(scenario):
    result = scenario.executor().execute(
        ToolCall(
            **call(
                "filter_student_group",
                conditions=[
                    {"dimension": "ALGORITHMS", "operator": "<=", "threshold": "2.50"},
                ],
            )
        )
    )
    assert result.facts[0].value == 2 and result.facts[0].denominator == 4
    assert all(ref.batch_id == "synthetic-batch" for ref in result.facts[0].scope.records)
    assert result.facts[0].conditions[0].dimension is Dimension.ALGORITHMS


def test_evidence_only_fallback_does_not_falsely_claim_evidence_was_not_read(scenario):
    outcome = scenario.run(
        FakeLLM(
            calls=[
                call("get_evidence_records", evidence_ids=["comment-alpha"]),
                call("get_rubric_criteria"),
            ]
        )
    )
    assert outcome.status == "insufficient_data"
    assert not outcome.report.observed_facts and not outcome.report.teaching_actions
    assert not any("未读取" in text or "未取得" in text for text in outcome.report.limitations)


def test_non_synthetic_declaration_denied_before_any_model_call(scenario):
    scenario.source = SyntheticInput(
        scenario.source.dataset,
        scenario.source.access.model_copy(update={"synthetic_only": False}),
    )
    client = FakeLLM()
    outcome = scenario.run(client)
    assert outcome.error_code == "synthetic_input_required" and not client.prompts


@pytest.mark.parametrize("unknown_score", [None, "synthetic-invalid"])
def test_filter_unknown_is_not_falsely_labelled_missing_or_zero(scenario, unknown_score):
    from app.analysis.edu import AssessmentRecord, ScorePolicy, prepare_dataset

    rows = [
        AssessmentRecord(
            item.reference.batch_id,
            item.reference.student_id,
            item.reference.record_id,
            item.reference.source_id,
            {score.dimension: score.raw_value for score in item.scores},
            item.reference.evidence_refs,
        )
        for item in scenario.source.dataset.records
    ]
    rows.append(
        AssessmentRecord(
            "synthetic-batch",
            "synthetic-unknown-cell",
            "synthetic-unknown-row",
            "synthetic-source",
            {Dimension.ALGORITHMS: unknown_score, Dimension.STRATEGY: 4},
        )
    )
    access = scenario.source.access.model_copy(
        update={
            "student_ids": (*scenario.source.access.student_ids, "synthetic-unknown-cell"),
        }
    )
    scenario.source = SyntheticInput(prepare_dataset(rows, policy=ScorePolicy()), access)
    result = scenario.executor().execute(
        ToolCall(
            **call(
                "filter_student_group",
                conditions=[
                    {"dimension": "ALGORITHMS", "operator": "<", "threshold": "3"},
                    {"dimension": "STRATEGY", "operator": ">=", "threshold": "4"},
                ],
            )
        )
    )
    from fractions import Fraction

    facts = {item.metric: item for item in result.facts}
    assert facts["matched_ratio"].value == Fraction(2, 5)
    assert facts["matched_ratio_eligible"].value == Fraction(1, 2)
    assert facts["matched_ratio"].unevaluable_count == 1
    assert facts["matched_ratio"].missing_count is None
    assert facts["matched_ratio"].invalid_count is None
