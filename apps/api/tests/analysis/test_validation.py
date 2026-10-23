from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from app.analysis.edu import (
    AnalysisInputError,
    AssessmentRecord,
    Dimension,
    ScorePolicy,
    ScoreState,
    prepare_dataset,
    summarize,
)

D = Dimension.ALGORITHMS


@pytest.mark.parametrize(
    "raw", [0, 6, -1, True, "3", "", float("nan"), float("inf"), Decimal("NaN")]
)
def test_invalid_scores_are_reported_not_clipped_or_zero_filled(
    make_record: Callable[..., AssessmentRecord], raw: object
) -> None:
    record = make_record("synthetic-invalid", overrides={D: raw})
    data = prepare_dataset([record], policy=ScorePolicy())
    score = data.records[0].score(D)
    assert score.state is ScoreState.INVALID
    assert score.value is None
    assert data.issues[0].score is score
    assert data.issues[0].reference.record_id == record.record_id
    stat = summarize(data, batch_id="synthetic-batch").dimension(D)
    assert (stat.valid_count, stat.missing_count, stat.invalid_count) == (0, 0, 1)
    assert stat.mean is None
    assert stat.distribution == ()


def test_missing_absent_and_configured_tokens_are_explicit(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    absent = make_record("synthetic-absent")
    absent = replace(
        absent, scores={key: value for key, value in absent.scores.items() if key is not D}
    )
    data = prepare_dataset(
        [
            absent,
            make_record("synthetic-none", overrides={D: None}),
            make_record("synthetic-token", overrides={D: "NA"}),
        ],
        policy=ScorePolicy(missing_tokens=("NA",)),
    )
    stat = summarize(data, batch_id="synthetic-batch").dimension(D)
    assert (stat.valid_count, stat.missing_count, stat.invalid_count) == (0, 3, 0)
    assert all(issue.score.state is ScoreState.MISSING for issue in data.issues)


def test_half_scores_and_step_policy_are_exact_and_configurable(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    row = make_record("synthetic-half", overrides={D: 2.5})
    data = prepare_dataset([row], policy=ScorePolicy(step=Fraction(1, 2)))
    assert data.records[0].score(D).value == Fraction(5, 2)
    quarter = make_record("synthetic-quarter", overrides={D: 2.25})
    assert prepare_dataset([quarter], policy=ScorePolicy()).records[0].score(D).value == Fraction(
        9, 4
    )
    rejected = prepare_dataset([quarter], policy=ScorePolicy(step=Fraction(1, 2)))
    assert rejected.records[0].score(D).reason == "off_step"
    assert rejected.records[0].score(D).raw_value == 2.25


def test_custom_range_and_step_use_minimum_as_origin(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    policy = ScorePolicy(minimum=Fraction(0), maximum=Fraction(10), step=Fraction(1, 4))
    rows = [
        make_record("synthetic-zero", overrides={D: 0}),
        make_record("synthetic-large", overrides={D: Decimal("8.75")}),
    ]
    stat = summarize(prepare_dataset(rows, policy=policy), batch_id="synthetic-batch").dimension(D)
    assert stat.mean == Fraction(35, 8)
    assert stat.valid_count == 2


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_student_in_one_batch_rejects_entire_dataset(
    make_record: Callable[..., AssessmentRecord], conflicting: bool
) -> None:
    first = make_record("synthetic-duplicate")
    second = make_record("synthetic-duplicate", overrides={D: 5}) if conflicting else first
    with pytest.raises(AnalysisInputError, match="duplicate_student_in_batch"):
        prepare_dataset([first, second], policy=ScorePolicy())


def test_duplicate_source_locator_cannot_identify_two_students(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    with pytest.raises(AnalysisInputError, match="duplicate_record_reference"):
        prepare_dataset(
            [
                make_record("synthetic-a", record_id="same-row"),
                make_record("synthetic-b", record_id="same-row"),
            ],
            policy=ScorePolicy(),
        )


def test_different_batches_can_reuse_student_and_row_ids(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    rows = [
        make_record("synthetic-same", batch="batch-a", overrides={D: 1}),
        make_record("synthetic-same", batch="batch-b", overrides={D: 5}),
    ]
    data = prepare_dataset(rows, policy=ScorePolicy())
    assert summarize(data, batch_id="batch-a").dimension(D).mean == 1
    assert summarize(data, batch_id="batch-b").dimension(D).mean == 5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum": 5, "maximum": 1},
        {"step": 0},
        {"step": -1},
        {"maximum": float("inf")},
        {"minimum": True},
        {"missing_tokens": [None]},
    ],
)
def test_invalid_policy_is_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(AnalysisInputError):
        ScorePolicy(**kwargs)


def test_structural_errors_are_rejected_before_analysis(
    make_record: Callable[..., AssessmentRecord],
) -> None:
    row = make_record("synthetic-a")
    for malformed in [
        replace(row, student_id=" "),
        replace(row, source_id=""),
        replace(row, scores={"unknown": 3}),
        replace(row, scores={D: [3]}),
    ]:
        with pytest.raises(AnalysisInputError):
            prepare_dataset([malformed], policy=ScorePolicy())


def test_input_score_mapping_is_snapshotted(make_record: Callable[..., AssessmentRecord]) -> None:
    row = make_record("synthetic-a")
    scores = dict(row.scores)
    data = prepare_dataset([replace(row, scores=scores)], policy=ScorePolicy())
    scores[D] = 5
    assert data.records[0].score(D).value == 1
