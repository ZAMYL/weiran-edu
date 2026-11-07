"""Disclosure-only narrative inputs and exact teacher display regression."""

import json
from dataclasses import asdict
from fractions import Fraction

import pytest
from prototypes.capability_profiles.demo import _display
from prototypes.capability_profiles.engine import POLICY
from prototypes.capability_profiles.explanations import display_score, explain, explanation_input

from app.edu.analysis import stat


@pytest.mark.parametrize(
    "value,expected",
    [
        (Fraction(21, 8), "2.63"),
        (Fraction(-21, 8), "-2.63"),
        (Fraction(1, 200), "0.01"),
        (Fraction(-1, 200), "-0.01"),
        (Fraction(-1, 1000), "0.00"),
        (Fraction(0), "0.00"),
        (Fraction(647, 210), "3.08"),
        (None, "未提供"),
    ],
)
def test_teacher_rounding_matches_formal_half_up_without_changing_exact_values(value, expected):
    original = value
    exact = (
        None if value is None else {"numerator": value.numerator, "denominator": value.denominator}
    )
    assert display_score(value) == _display(exact) == expected
    if value is not None:
        assert stat(value).display == expected
    assert value == original


def test_narrative_whitelist_cannot_obtain_hidden_payloads():
    class NeverRead:
        def __iter__(self):
            raise AssertionError("hidden_distribution_read")

    def number(value):
        return {"numerator": value.numerator, "denominator": value.denominator}

    view = {
        "metrics": {
            "student_denominator": 96,
            "target_count": 48,
            "remainder_count": 48,
            "unevaluable_count": 0,
        },
        "chart": [
            {
                "dimension": "IMPLEMENTATION",
                "condition_defined": False,
                "target": {
                    "mean": number(Fraction(21, 8)),
                    "valid_count": 48,
                    "distribution": NeverRead(),
                    "distribution_state": "privacy_limited",
                },
                "remainder": {
                    "mean": number(Fraction(17, 8)),
                    "valid_count": 48,
                    "distribution": NeverRead(),
                    "distribution_state": "privacy_limited",
                },
                "difference": number(Fraction(1, 2)),
                "witness": NeverRead(),
            }
        ],
        "members": NeverRead(),
        "source_locators": NeverRead(),
    }
    approved = explanation_input(view)
    snapshot = json.dumps(asdict(approved), default=str)
    assert not any(k in snapshot for k in ("distribution", "witness", "members", "source_locators"))
    result = explain(approved, POLICY, "已核验的合成条件")
    assert "2.63分" in result["summary"] and "2.13分" in result["summary"]
    assert "高0.50分" in result["summary"]
    with pytest.raises(TypeError, match="permitted_explanation_input_required"):
        explain(view, POLICY, "不得直接解释原始JSON")
