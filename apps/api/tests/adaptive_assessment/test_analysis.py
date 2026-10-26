"""Hand-checkable values, not snapshots manufactured from the implementation."""

from fractions import Fraction as F

import pytest
from prototypes.adaptive_assessment.adapters import parse_contest_zip
from prototypes.adaptive_assessment.agent import Predicate
from prototypes.adaptive_assessment.analysis import Kernel
from prototypes.adaptive_assessment.data import PrototypeError
from prototypes.adaptive_assessment.synthetic import admit, contest_zip

from .test_adapters import mutate


def kernel(name="contest13"):
    return Kernel(admit(name).envelope)


def test_two_schemes_have_independent_statistics_and_scales():
    a, _ = kernel("five").statistics("I01")
    b, _ = kernel().statistics("Q01")
    assert (a["denominator"], a["valid_count"], a["mean"], a["median"]) == (
        53,
        53,
        F(161, 53),
        F(5),
    )
    assert a["distribution"] == ((F(1), 26), (F(5), 27))
    assert (b["denominator"], b["mean"], b["median"], b["distribution"]) == (
        20,
        F(50),
        F(50),
        ((F(20), 10), (F(80), 10)),
    )


def test_group_means_and_actual_sources_for_five_and_questions():
    for name, left, outcome, threshold, count, total, diff in (
        ("five", "I01", "I02", "1", 26, 53, 4),
        ("contest13", "Q01", "Q02", "20", 10, 20, 60),
    ):
        result = kernel(name).group(
            [Predicate(item_id=left, operator="<=", threshold=threshold)], outcome
        )
        assert result.qualified
        assert result.values["target_count"] == count and result.values["denominator"] == total
        assert result.values["difference"] == diff
        assert result.values["share_total"] == F(count, total)
        target = next(s for s in result.sources if s.key == "target.valid")
        rest = next(s for s in result.sources if s.key == "remainder.valid")
        assert len(target.references) == count and len(rest.references) == total - count
        assert not set(target.participants) & set(rest.participants)
        assert result.ranking["robustness"]["passed"] == 3


def test_pair_spearman_uses_same_ranks_with_different_original_scales():
    for name, left, right, n in (("five", "I01", "I02", 53), ("contest13", "Q01", "Q02", 20)):
        result = kernel(name).relation(left, right)
        assert result.values["rho"] == pytest.approx(-1)
        assert result.values["paired_count"] == n and result.qualified
        assert all(len(s.references) == n for s in result.sources)


def test_missing_is_not_zero_and_only_valid_participants_support_mean():
    k = kernel()
    result, source = k.statistics("Q13")
    # Ten scores80 + five scores20; last five entries are missing.
    assert result["mean"] == F(60) and result["valid_count"] == 15
    assert result["states"] == {"missing": 5, "valid": 15}
    assert len(source.participants) == 15 and "S0019" not in source.participants
    quality = k.quality("Q13")
    assert quality.values["score_changed"] is False
    assert len(next(s for s in quality.sources if s.key == "state.missing").participants) == 5


def test_unknown_scale_preserves_raw_descriptions_but_refuses_unsupported_inference():
    k = kernel("limited")
    result, _ = k.statistics("Q12")
    assert result["mean"] == 50 and result["states"] == {"range_unknown": 20}
    assert result["range_checked"] is False
    with pytest.raises(PrototypeError, match="relation_scale_or_level_unknown"):
        k.relation("Q12", "Q01")
    with pytest.raises(PrototypeError, match="filter_scale_unknown"):
        k.group([Predicate(item_id="Q12", operator="<=", threshold="20")], "Q01")
    with pytest.raises(PrototypeError, match="outcome_scale_or_level_unknown"):
        k.group([Predicate(item_id="Q01", operator="<=", threshold="20")], "Q12")


def test_absent_withheld_unconfirmed_evidence_not_used_for_numeric_statistics():
    raw = contest_zip(items=3, missing=False)
    for status in ("absent", "withheld", "unconfirmed"):
        # All twenty score cells for Q01 declare the corresponding synthetic source state.
        from .test_adapters import pack, unpack

        parts = unpack(raw)
        for name in parts:
            if name.startswith("answers/"):
                parts[name] = parts[name].replace(
                    b'<td data-field="evidence">available',
                    f'<td data-field="evidence">{status}'.encode(),
                    1,
                )
        k = Kernel(parse_contest_zip(pack(parts)))
        stats, source = k.statistics("Q01")
        assert stats["valid_count"] == 0 and stats["mean"] is None
        assert source.references == ()
        assert stats["states"] == {"evidence_" + status: 20}


def test_decimal_boundary_and_legal_zero_are_exact():
    raw = contest_zip(missing=False)
    raw = mutate(
        "answers/S0000.html", b'<td data-field="score">80', b'<td data-field="score">2.75', raw
    )
    raw = mutate(
        "answers/S0001.html", b'<td data-field="score">80', b'<td data-field="score">0', raw
    )
    k = Kernel(parse_contest_zip(raw))
    stats, _ = k.statistics("Q01")
    assert stats["mean"] == F(20 * 50 - 160, 20) + F(11, 80)
    assert stats["valid_count"] == 20
    strict = k.group([Predicate(item_id="Q01", operator="<", threshold="2.75")], "Q02")
    inclusive = k.group([Predicate(item_id="Q01", operator="<=", threshold="2.75")], "Q02")
    assert strict.values["target_count"] == 1 and inclusive.values["target_count"] == 2
    assert not strict.qualified and not inclusive.qualified


def test_constant_and_insufficient_pair_are_diagnostics_not_cards():
    k = kernel("small")
    result = k.relation("Q01", "Q02")
    assert (
        result.values["state"] == "insufficient_pairs"
        and result.values["rho"] is None
        and not result.qualified
    )
    raw = contest_zip(missing=False)
    from .test_adapters import pack, unpack

    parts = unpack(raw)
    for name in parts:
        if name.startswith("answers/"):
            parts[name] = parts[name].replace(
                b'<td data-field="score">20', b'<td data-field="score">80'
            )
    constant = Kernel(parse_contest_zip(pack(parts))).relation("Q01", "Q02")
    assert constant.values["state"] == "constant_input" and not constant.qualified


def test_invalid_scores_isolated_without_silent_rounding():
    raw = contest_zip(missing=False)
    raw = mutate(
        "answers/S0000.html", b'<td data-field="score">80', b'<td data-field="score">101', raw
    )
    raw = mutate(
        "answers/S0001.html", b'<td data-field="score">80', b'<td data-field="score">2.751', raw
    )
    stats, source = Kernel(parse_contest_zip(raw)).statistics("Q01")
    assert stats["states"] == {"off_step": 1, "out_of_range": 1, "valid": 18}
    assert stats["valid_count"] == 18 and len(source.references) == 18


def test_conditions_do_not_become_outcomes_and_zero_group_is_not_discovery():
    k = kernel()
    condition = Predicate(item_id="Q01", operator="<", threshold="0")
    result = k.group([condition], "Q02")
    assert result.values["target_count"] == 0 and result.values["difference"] is None
    assert not result.qualified and result.values["share_total"] == 0
    with pytest.raises(PrototypeError, match="condition_and_outcome"):
        k.group([condition], "Q01")


def test_coarse_distributions_keep_original_units_and_denominators():
    bins, sources = kernel().coarse("Q01")
    assert [b["count"] for b in bins] == [10, 0, 0, 10]
    assert bins[0]["lower"] == 0 and bins[-1]["upper"] == 100
    assert sum(len(s.participants) for s in sources) == 20


def test_two_condition_and_results_are_computed_and_disjoint():
    k = kernel()
    a = Predicate(item_id="Q01", operator="<=", threshold="20")
    b = Predicate(item_id="Q03", operator="<=", threshold="20")
    result = k.group([a, b], "Q02")
    assert result.values["target_count"] == 10 and result.values["difference"] == 60
    c = Predicate(item_id="Q02", operator="<=", threshold="20")
    empty = k.group([a, c], "Q03")
    assert empty.values["target_count"] == 0 and empty.values["remainder_count"] == 20
    assert not empty.qualified


def test_each_question_keeps_its_actual_scale_without_automatic_summing():
    from .test_adapters import pack, unpack

    parts = unpack(contest_zip(missing=False))
    # Edit only Q02's actual source scale and score cells, not a computed normalization.
    import re

    for name, raw in parts.items():

        def update(match):
            value = match.group(0).replace(b'<td data-field="max">100', b'<td data-field="max">50')
            value = value.replace(b'<td data-field="score">80', b'<td data-field="score">40')
            return value.replace(b'<td data-field="score">20', b'<td data-field="score">10')

        parts[name] = re.sub(rb'<tr data-question="Q02">.*?</tr>', update, raw)
    k = Kernel(parse_contest_zip(pack(parts)))
    assert k.items["Q01"].scale.maximum == "100" and k.items["Q02"].scale.maximum == "50"
    group = k.group([Predicate(item_id="Q01", operator="<=", threshold="20")], "Q02")
    assert group.values["target_mean"] == 40 and group.values["remainder_mean"] == 10
    assert group.values["difference"] == 30
    assert k.relation("Q01", "Q02").values["rho"] == pytest.approx(-1)
    assert "total_score" not in group.values


def test_empty_and_unknown_measurement_do_not_create_qualified_results():
    k = kernel("empty")
    stats, _ = k.statistics("Q01")
    assert stats["denominator"] == 0 and stats["mean"] is None
    assert not k.quality("Q02").qualified
    raw = mutate(
        "manifest.html", b'<td data-field="level">interval', b'<td data-field="level">unknown'
    )
    unknown = Kernel(parse_contest_zip(raw))
    assert not unknown.catalog()["items"][0]["filter_eligible"]
    with pytest.raises(PrototypeError, match="filter_level_unknown"):
        unknown.group([Predicate(item_id="Q01", operator="<=", threshold="20")], "Q02")
