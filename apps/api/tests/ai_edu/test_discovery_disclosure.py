"""Independent >=20-person counterexamples for same-tool disclosure transactions."""

from fractions import Fraction

import pytest

from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.discovery.tools import ToolExecutor, parse_arguments
from app.analysis.edu import prepare_dataset
from app.analysis.edu.discovery import TargetedDiscovery
from app.analysis.edu.models import AssessmentRecord, ScorePolicy
from app.analysis.edu.models import Dimension as D


def make_executor(rows, **limits):
    records = tuple(
        AssessmentRecord(
            "synthetic-batch",
            f"synthetic-{i:03}",
            f"record-{i:03}",
            "synthetic-source",
            dict(zip(D, row, strict=True)),
        )
        for i, row in enumerate(rows)
    )
    dataset = prepare_dataset(records, policy=ScorePolicy(step=Fraction(1, 100)))
    builder = TargetedDiscovery(
        dataset, batch_id="synthetic-batch", snapshot_id="synthetic-snapshot"
    )
    return ToolExecutor(builder, "synthetic-run", Limits(**limits))


def execute(executor, name, arguments=None):
    return executor.execute(name, parse_arguments(name, arguments or {}))


@pytest.mark.parametrize("delta", [1, 2, 3, 4])
def test_summary_dimensions_share_same_execution_staging(delta):
    # N20; A valid15/missing5, B valid(15-delta)/missing(5+delta).
    # Each set independently differs from the cohort by >=k, but A xor B has delta people.
    rows = [(None if i < 5 else 3, None if i < 5 + delta else 3, 3, 3, 3) for i in range(20)]
    e = make_executor(rows)
    fact = execute(e, "get_dimension_statistics")[0]
    dims = {d["dimension"]: d for d in fact.view["dimensions"]}
    assert dims["ALGORITHMS"]["valid_count"] == 15
    assert dims["IMPLEMENTATION"] == {
        "dimension": "IMPLEMENTATION",
        "state": "privacy_or_data_limited",
    }
    assert not any(s.metric_key == "stats.IMPLEMENTATION" for s in fact.metric_sources)
    assert frozenset(e.builder.scope.records[5:]) in e.released
    assert frozenset(e.builder.scope.records[5 + delta :]) not in e.released


@pytest.mark.parametrize("delta", [1, 2, 3, 4])
def test_group_members_checked_against_outcome_valid_records(delta):
    rows = [
        (1 if i < 10 else 5, None if i < delta else (5 if i < 10 else 1), 3, 3, 3)
        for i in range(20)
    ]
    e = make_executor(rows)
    before = (dict(e.facts), list(e.released), e.output_bytes, set(e.discoveries))
    args = {
        "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": "1"}],
        "outcomes": ["IMPLEMENTATION"],
    }
    with pytest.raises(AgentError, match="privacy_limited"):
        execute(e, "compare_student_groups", args)
    assert (e.facts, e.released, e.output_bytes, e.discoveries) == before
    # Attempts and actual elapsed work remain auditable, not refunded publication bytes.
    assert len(e.seen) == 1 and e.compute_seconds > 0


@pytest.mark.parametrize("delta", [1, 2, 3, 4])
def test_multiple_outcomes_checked_against_earlier_outcome(delta):
    # N40, target20/rest20. A result valid15 versus another valid(15-delta)
    # passes both cohort/member checks separately; only cumulative staging sees the gap.
    rows = [
        (
            1 if i < 20 else 5,
            None if i < 5 else (5 if i < 20 else 1),
            None if i < 5 + delta else (5 if i < 20 else 1),
            3,
            3,
        )
        for i in range(40)
    ]
    e = make_executor(rows)
    args = {
        "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": "1"}],
        "outcomes": ["IMPLEMENTATION", "MODELING"],
    }
    with pytest.raises(AgentError, match="privacy_limited"):
        execute(e, "compare_student_groups", args)
    assert not e.facts and e.output_bytes == 0 and not e.discoveries
    assert e.released == [frozenset(e.builder.scope.records)]


def test_permitted_gap_of_five_is_not_overblocked():
    rows = [(None if i < 5 else 3, None if i < 10 else 3, 3, 3, 3) for i in range(20)]
    fact = execute(make_executor(rows), "get_dimension_statistics")[0]
    available = {
        d["dimension"]: d["valid_count"]
        for d in fact.view["dimensions"]
        if d["state"] == "available"
    }
    assert available["ALGORITHMS"] == 15 and available["IMPLEMENTATION"] == 10


def test_failed_tool_preserves_previously_published_state():
    rows = [
        (1 if i < 10 else 5, None if i == 0 else (5 if i < 10 else 1), 3, 3, 3) for i in range(20)
    ]
    e = make_executor(rows)
    execute(e, "get_dimension_statistics")
    before = (dict(e.facts), list(e.released), e.output_bytes, set(e.discoveries))
    with pytest.raises(AgentError, match="privacy_limited"):
        execute(
            e,
            "compare_student_groups",
            {
                "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": "1"}],
                "outcomes": ["IMPLEMENTATION"],
            },
        )
    assert (e.facts, e.released, e.output_bytes, e.discoveries) == before


def test_gate_failure_does_not_mutate_staging_or_released_state():
    e = make_executor([(3, 3, 3, 3, 3)] * 20)
    refs = e.builder.scope.records
    staging = e._gate([refs[:10]], staged=[])
    before = list(staging)
    with pytest.raises(AgentError, match="privacy_limited"):
        e._gate([refs[10:], refs[:11]], staged=staging)
    assert staging == before
    assert e.released == [frozenset(refs)]


def test_late_projection_failure_cannot_publish_first_of_two_facts():
    from unittest.mock import patch

    from app.ai.edu.discovery.projection import model_discovery

    rows = [(1, 5, 5, 3, 3)] * 20 + [(5, 1, 1, 3, 3)] * 20
    e = make_executor(rows)
    original = model_discovery
    calls = 0

    def fail_second(discovery):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AgentError("unsupported_model_projection")
        return original(discovery)

    with patch("app.ai.edu.discovery.tools.model_discovery", side_effect=fail_second):
        with pytest.raises(AgentError, match="unsupported_model_projection"):
            execute(
                e,
                "compare_student_groups",
                {
                    "conditions": [{"dimension": "ALGORITHMS", "operator": "<=", "threshold": "1"}],
                    "outcomes": ["IMPLEMENTATION", "MODELING"],
                },
            )
    assert calls == 2 and not e.facts and not e.discoveries and e.output_bytes == 0
    assert e.released == [frozenset(e.builder.scope.records)]


def test_output_limit_rolls_back_all_staged_releases():
    rows = [(None if i < 5 else 3, 3, 3, 3, 3) for i in range(20)]
    e = make_executor(rows, output_bytes=100)
    with pytest.raises(AgentError, match="tool_output_limit"):
        execute(e, "get_dimension_statistics")
    assert e.output_bytes == 0 and not e.facts and not e.discoveries
    assert e.released == [frozenset(e.builder.scope.records)]
