"""All input data is constructed here; no runtime application imports."""

import json
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from .model import PROFILE, Scale, exact
from .readiness import AnalysisCapability, AnalysisReadiness, assess, score_state
from .synthetic import fixture, scenarios


def capabilities(
    result: AnalysisReadiness, analysis: str, state: str = "available_analyses"
) -> list[AnalysisCapability]:
    return [c for c in getattr(result, state) if c.analysis == analysis]


def test_a_three_items_without_comments_can_continue():
    result = assess(scenarios()["A_three_items_no_comments"])
    assert len(result.recognized_items) == 3
    assert len(capabilities(result, "item_statistics")) == 3
    suggestion = next(s for s in result.suggested_resources if s.scope == "profile")
    assert "效率意识" in suggestion.missing and "解题策略" in suggestion.missing
    assert "算法与数据结构" not in suggestion.missing
    assert suggestion.requirement == "optional" and suggestion.can_continue
    assert not result.quality_issues


def test_b_complete_five_can_explore_and_prepare_review():
    result = assess(scenarios()["B_five_items_review_resources"])
    assert len(capabilities(result, "item_statistics")) == 5
    assert len(capabilities(result, "item_combination_exploration")) == 10
    assert len(capabilities(result, "evaluation_review")) == 5
    assert not result.missing_fields
    assert not result.suggested_resources


def test_c_eight_custom_items_are_never_truncated_or_mapped():
    result = assess(scenarios()["C_eight_custom_items"])
    assert len(result.recognized_items) == 8
    assert len(capabilities(result, "item_statistics")) == 8
    assert all(i["confirmed_profile_code"] is None for i in result.recognized_items)


def test_d_thirteen_questions_do_not_create_competencies():
    result = assess(scenarios()["D_thirteen_questions"])
    assert len(result.recognized_items) == 13
    assert len(capabilities(result, "item_statistics")) == 13
    assert {i["kind"] for i in result.recognized_items} == {"question"}
    assert all(i["confirmed_profile_code"] is None for i in result.recognized_items)
    assert all(i["scale"]["unit"] == "points" for i in result.recognized_items)


def test_e_no_response_does_not_block_scores():
    result = assess(scenarios()["E_scores_without_responses"])
    assert len(capabilities(result, "item_statistics")) == 5
    assert not capabilities(result, "evaluation_review")
    assert any("答卷" in s.missing and s.can_continue for s in result.suggested_resources)


def test_f_missing_cell_does_not_remove_defined_item():
    result = assess(scenarios()["F_defined_five_missing_score"])
    assert len(result.recognized_items) == 5
    first = result.recognized_items[0]
    assert first["quality_counts"] == {"missing": 1, "valid": 2}
    partial = capabilities(result, "item_statistics", "partially_available_analyses")
    assert [(c.eligible_count, c.denominator) for c in partial] == [(2, 3)]
    assert not any(s.scope == "profile" for s in result.suggested_resources)


def test_g_no_criteria_does_not_invent_basis():
    result = assess(scenarios()["G_no_criteria"])
    assert not capabilities(result, "evaluation_review")
    assert len(capabilities(result, "item_statistics")) == 5
    assert any("评价标准" in s.missing for s in result.suggested_resources)


def test_h_repeated_assessments_do_not_prove_progress():
    result = assess(scenarios()["H_history_without_comparability"])
    assert len(capabilities(result, "longitudinal_progress", "unavailable_analyses")) == 1
    assert any(s.scope == "longitudinal_progress" for s in result.suggested_resources)


@pytest.mark.parametrize("n", [1, 7, 19])
@pytest.mark.parametrize("item_count", [1, 3, 8, 13])
def test_counts_come_from_data(n, item_count):
    result = assess(fixture(item_count, n))
    stats = capabilities(result, "item_statistics")
    assert len(stats) == item_count
    assert all((c.eligible_count, c.denominator) == (n, n) for c in stats)


@pytest.mark.parametrize(
    "raw,code",
    [
        ("6", "out_of_range"),
        ("0", "out_of_range"),
        ("2.751", "off_step"),
        ("NaN", "invalid_numeric"),
        ("Infinity", "invalid_numeric"),
        ("abc", "invalid_numeric"),
        (True, "invalid_numeric"),
        (2.75, "invalid_numeric"),
    ],
)
def test_invalid_scores_are_blocked_and_never_zero_filled(raw, code):
    dataset = fixture(1)
    observation = replace(dataset.observations[0], raw_decimal=raw)
    result = assess(replace(dataset, observations=(observation, *dataset.observations[1:])))
    assert result.quality_issues[0]["code"] == code
    partial = capabilities(result, "item_statistics", "partially_available_analyses")
    assert (partial[0].eligible_count, partial[0].denominator) == (2, 3)
    assert any(s.requirement == "quality_blocker" for s in result.suggested_resources)


def test_authorization_is_per_purpose_and_can_be_revoked():
    dataset = fixture(1, comments=True, responses=True, criteria=True)
    assert capabilities(assess(dataset), "evaluation_review")
    external = assess(dataset, "external_model")
    assert not external.available_analyses
    assert any(i["code"] == "score_evidence_withheld" for i in external.quality_issues)
    revoked = replace(
        dataset, evidence=tuple(replace(e, permitted_purposes=()) for e in dataset.evidence)
    )
    assert not assess(revoked).available_analyses
    assert len(assess(revoked).recognized_items) == 1


def test_withheld_comment_is_not_absent_and_cannot_enable_review():
    dataset = fixture(1, comments=True, responses=True, criteria=True)
    withheld = replace(
        dataset,
        evidence=tuple(
            replace(e, permitted_purposes=()) if e.kind == "comment" else e
            for e in dataset.evidence
        ),
    )
    assert any(e.kind == "comment" for e in withheld.evidence)
    assert capabilities(assess(withheld), "item_statistics")
    assert not capabilities(assess(withheld), "evaluation_review")


def test_evidence_must_belong_to_the_observation():
    dataset = fixture(1, 2)
    forged = replace(dataset.observations[0], evidence_ids=dataset.observations[1].evidence_ids)
    with pytest.raises(ValueError, match="observation_evidence_mismatch"):
        assess(replace(dataset, observations=(forged, dataset.observations[1])))


def test_duplicate_and_unknown_items_fail_closed():
    dataset = fixture(1)
    with pytest.raises(ValueError, match="invalid_or_duplicate_id"):
        assess(replace(dataset, items=dataset.items * 2))
    with pytest.raises(ValueError, match="duplicate_or_unknown_observation_target"):
        assess(
            replace(
                dataset,
                observations=(
                    replace(dataset.observations[0], item_id="unknown"),
                    *dataset.observations[1:],
                ),
            )
        )


def test_empty_roster_does_not_claim_zero_scores_or_ready_analysis():
    result = assess(fixture(3, 0))
    assert not result.available_analyses and not result.partially_available_analyses
    assert all(c.denominator == 0 for c in result.unavailable_analyses)


def test_missing_scale_allows_raw_scores_but_not_range_claims():
    dataset = fixture(1)
    dataset = replace(dataset, items=(replace(dataset.items[0], scale=None),))
    result = assess(dataset)
    assert capabilities(result, "item_statistics")
    assert result.recognized_items[0]["quality_counts"] == {"range_unknown": 3}
    assert any(s.missing.endswith("原量表范围、单位和步长") for s in result.suggested_resources)


def test_exact_precision_and_original_scales_are_preserved():
    assert exact("2.75") == Fraction(11, 4)
    assert exact("2.750000000000000000001") != exact("2.75")
    dataset = fixture(2)
    alternative = Scale("different-scale", "0", "100", "0.25", "points")
    item = replace(dataset.items[1], scale=alternative)
    dataset = replace(dataset, items=(dataset.items[0], item))
    result = assess(dataset)
    assert [i["scale"]["maximum"] for i in result.recognized_items] == ["5", "100"]
    assert score_state(replace(dataset.observations[0], raw_decimal="2.751"), item) == "off_step"
    assert "total_score" not in json.dumps(result.to_dict())


def test_profile_mapping_requires_explicit_confirmation_even_with_same_name():
    dataset = fixture(1)
    item = replace(dataset.items[0], confirmed_profile_code=None)
    result = assess(replace(dataset, items=(item,)))
    assert result.recognized_items[0]["name"] == PROFILE["algorithm_data_structure"]
    assert result.recognized_items[0]["confirmed_profile_code"] is None
    assert any("算法与数据结构" in s.missing for s in result.suggested_resources)


def test_synthetic_output_is_json_and_does_not_include_raw_content():
    for dataset in scenarios().values():
        encoded = json.dumps(assess(dataset).to_dict(), ensure_ascii=False)
        assert "raw_decimal" not in encoded
        assert "permitted_purposes" not in encoded


def test_assumed_score_sources_cannot_enable_analysis():
    dataset = fixture(1)
    assumed = replace(
        dataset, evidence=tuple(replace(e, provenance="assumption") for e in dataset.evidence)
    )
    assert not assess(assumed).available_analyses


def test_pairing_requires_same_participants_not_two_nonempty_items():
    dataset = fixture(2, 2)
    observations = tuple(
        replace(o, raw_decimal=None)
        if (o.participant_id, o.item_id)
        in (("synthetic-p-0", "synthetic-item-0"), ("synthetic-p-1", "synthetic-item-1"))
        else o
        for o in dataset.observations
    )
    result = assess(replace(dataset, observations=observations))
    assert len(capabilities(result, "item_statistics", "partially_available_analyses")) == 2
    assert not capabilities(result, "item_combination_exploration")
    assert capabilities(result, "item_combination_exploration", "unavailable_analyses")


def test_no_observation_is_missing_in_a_defined_item():
    dataset = fixture(1)
    result = assess(replace(dataset, observations=dataset.observations[1:]))
    assert len(result.recognized_items) == 1
    assert result.recognized_items[0]["quality_counts"] == {"missing": 1, "valid": 2}


def test_withheld_score_is_not_inspected_for_quality():
    dataset = fixture(1, 1)
    dataset = replace(
        dataset,
        observations=(replace(dataset.observations[0], raw_decimal="secret-invalid"),),
        evidence=tuple(replace(e, permitted_purposes=()) for e in dataset.evidence),
    )
    result = assess(dataset)
    assert {issue["code"] for issue in result.quality_issues} == {"score_evidence_withheld"}
    assert "secret-invalid" not in json.dumps(result.to_dict())


def test_standard_scope_and_evidence_source_must_match():
    dataset = fixture(2, criteria=True)
    with pytest.raises(ValueError, match="rubric_scope_mismatch"):
        assess(
            replace(
                dataset,
                items=(
                    replace(dataset.items[0], rubric_id=dataset.items[1].rubric_id),
                    dataset.items[1],
                ),
            )
        )
    with pytest.raises(ValueError, match="invalid_evidence_scope"):
        assess(
            replace(
                dataset,
                evidence=(replace(dataset.evidence[0], source_id="unknown"), *dataset.evidence[1:]),
            )
        )


def test_unknown_scale_never_enables_full_evaluation_review():
    dataset = fixture(1, comments=True, responses=True, criteria=True)
    result = assess(replace(dataset, items=(replace(dataset.items[0], scale=None),)))
    assert capabilities(result, "item_statistics")
    assert not capabilities(result, "evaluation_review")


def test_source_order_does_not_change_readiness_membership_or_counts():
    dataset = fixture(3, 7)
    forward = assess(dataset)
    reverse = assess(
        replace(
            dataset,
            observations=tuple(reversed(dataset.observations)),
            evidence=tuple(reversed(dataset.evidence)),
        )
    )
    assert forward == reverse


def test_suggestions_only_request_resources_that_are_actually_unavailable():
    result = assess(scenarios()["E_scores_without_responses"])
    assert not any("评语" in s.missing for s in result.suggested_resources)
    assert sum("原始答卷" in s.missing for s in result.suggested_resources) == 5
    dataset = fixture(1, comments=True, responses=True, criteria=True)
    withheld = replace(
        dataset,
        evidence=tuple(
            replace(e, permitted_purposes=()) if e.kind == "response" else e
            for e in dataset.evidence
        ),
    )
    result = assess(withheld)
    assert any("申请 3 条已有原始答卷" in s.missing for s in result.suggested_resources)
    assert not any("补充" in s.missing for s in result.suggested_resources)


def test_mixed_kinds_and_sources_are_preserved():
    dataset = fixture(3)
    items = (
        dataset.items[0],
        replace(dataset.items[1], kind="question", confirmed_profile_code=None),
        replace(dataset.items[2], kind="task", confirmed_profile_code=None),
    )
    evidence = tuple(
        replace(e, source_id="synthetic-rubric-source") if e.item_id == items[1].item_id else e
        for e in dataset.evidence
    )
    result = assess(replace(dataset, items=items, evidence=evidence))
    assert {i["kind"] for i in result.recognized_items} == {"competency", "question", "task"}
    assert len(capabilities(result, "item_statistics")) == 3
    assert len(dataset.observations) == 9


def test_checked_in_outputs_match_actual_execution():
    expected = json.loads(Path(__file__).with_name("synthetic_outputs.json").read_text("utf-8"))
    actual = json.loads(json.dumps({k: assess(v).to_dict() for k, v in scenarios().items()}))
    assert actual == expected
