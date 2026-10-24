import pytest

from app.ai.edu import Limits
from app.ai.edu.models import Proposal
from app.ai.edu.validation import parse_json

from .conftest import FakeLLM, call, proposal


def changed(mutator):
    def response(prompt):
        result = proposal(prompt)
        mutator(result)
        return result

    return response


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda p: p["observed_facts"].append({"fact_id": "fact-does-not-exist"}),
            "unknown_fact_reference",
        ),
        (
            lambda p: p["cited_evidence_ids"].append("unknown-evidence"),
            "unknown_evidence_reference",
        ),
        (
            lambda p: p["hypotheses"][0]["evidence_ids"].append("unknown-evidence"),
            "unknown_evidence_reference",
        ),
        (
            lambda p: p["teaching_actions"][0]["fact_ids"].append("fact-0005"),
            "unobserved_fact_basis",
        ),
        (lambda p: p["cited_fact_ids"].clear(), "citation_union_mismatch"),
        (lambda p: p["cited_fact_ids"].append(p["cited_fact_ids"][0]), "duplicate_citation"),
        (lambda p: p["hypotheses"][0].update(status="confirmed"), "invalid_model_output"),
        (lambda p: p["observed_facts"][0].update(value=999), "invalid_model_output"),
        (lambda p: p["observed_facts"][0].update(text="平均分为999"), "invalid_model_output"),
        (lambda p: p["hypotheses"][0].update(text="全部学生已经进步"), "invalid_model_output"),
        (
            lambda p: p["teaching_actions"][0].update(kind="raise_scores_by_2"),
            "invalid_model_output",
        ),
        (lambda p: p["teaching_actions"][0].update(fact_ids=[]), "invalid_model_output"),
        (lambda p: p.update(assessment_date="2026-01-01"), "invalid_model_output"),
    ],
)
def test_unknown_references_fabricated_numbers_and_prose_rejected(scenario, mutation, code):
    outcome = scenario.run(FakeLLM(response=changed(mutation)))
    assert outcome.status == "error" and outcome.error_code == code and outcome.report is None


@pytest.mark.parametrize(
    "response,code",
    [
        ("not json", "invalid_model_output"),
        ('{"observed_facts":[]}', "invalid_model_output"),
        ('{"unknown":NaN}', "invalid_json_constant"),
        ('{"a":1,"a":2}', "duplicate_json_key"),
        ("[]", "invalid_model_output"),
        ("x" * 32001, "model_output_limit"),
    ],
)
def test_invalid_model_serialization(scenario, response, code):
    outcome = scenario.run(FakeLLM(response=response))
    assert outcome.error_code == code and outcome.report is None


@pytest.mark.parametrize("phase", ["plan", "compose"])
def test_model_timeout(scenario, phase):
    outcome = scenario.run(FakeLLM(delay_phase=phase), limits=Limits(timeout_seconds=0.01))
    assert outcome.error_code == "model_timeout" and outcome.report is None
    assert len(outcome.trace) == (0 if phase == "plan" else 1)


@pytest.mark.parametrize("phase", ["plan", "compose"])
def test_model_exception_does_not_echo(scenario, phase, caplog):
    outcome = scenario.run(FakeLLM(phase_error=phase))
    assert outcome.error_code == "workflow_failure" and outcome.report is None
    assert "PRIVATE" not in repr(outcome) and "PRIVATE" not in caplog.text


def test_bad_plan_and_oversized_plan_prevent_any_tool(scenario):
    class BadPlan(FakeLLM):
        async def plan(self, prompt):
            return '{"calls":[{"name":"get_dimension_statistics","arguments":{},"sql":"bad"}]}'

    assert scenario.run(BadPlan()).error_code == "invalid_model_output"
    outcome = scenario.run(FakeLLM(calls=[call(), call()]), limits=Limits(max_tool_calls=1))
    assert outcome.error_code == "tool_call_limit" and not outcome.trace


def test_previous_run_evidence_ids_not_reusable(scenario):
    first = scenario.run(
        FakeLLM(
            calls=[
                call(),
                call("get_evidence_records", evidence_ids=["comment-alpha"]),
            ]
        )
    )
    assert first.status == "validated"
    second = scenario.run(
        FakeLLM(
            response=changed(lambda p: p["hypotheses"][0]["evidence_ids"].append("comment-alpha"))
        )
    )
    assert second.error_code == "unknown_evidence_reference"


def test_every_observation_is_generated_from_ledger(scenario):
    outcome = scenario.run()
    assert outcome.status == "validated"
    assert outcome.report.observed_facts[0].fact_id in {fact.fact_id for fact in outcome.facts}
    # The model supplies no prose; the exact program value must be rendered verbatim.
    assert "=23/8；" in outcome.report.observed_facts[0].text


def test_zero_match_is_a_real_fact_not_missing_data(scenario):
    client = FakeLLM(
        calls=[
            call(
                "filter_student_group",
                conditions=[{"dimension": "ALGORITHMS", "operator": ">", "threshold": "5"}],
            )
        ]
    )
    outcome = scenario.run(client)
    assert outcome.status == "validated"
    assert outcome.facts[0].value == 0 and outcome.facts[1].value == 0
    assert outcome.facts[1].denominator == 4


def test_empty_eligible_group_falls_back(scenario):
    outcome = scenario.run(
        FakeLLM(
            calls=[
                call(
                    "filter_student_group",
                    student_ids=[],
                    conditions=[{"dimension": "ALGORITHMS", "operator": "<", "threshold": "3"}],
                )
            ]
        )
    )
    assert outcome.status == "insufficient_data"
    assert outcome.report.hypotheses == ()


def test_schema_has_no_free_form_natural_language_or_numeric_claim_field():
    schema = Proposal.model_json_schema()
    forbidden = {"text", "value", "mean", "assessment_date"}
    for definition in schema["$defs"].values():
        assert not (forbidden & definition.get("properties", {}).keys())
    # Duplicate nested JSON keys cannot silently override a trusted citation.
    with pytest.raises(ValueError, match="duplicate_json_key"):
        parse_json(Proposal, '{"nested":{"fact_id":"known","fact_id":"invented"}}', 32000)
