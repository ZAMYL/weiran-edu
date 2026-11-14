"""The summary must stay deterministic and agree with the published statistics."""

import pytest

from app.edu.analysis_models import DimensionStatistics, ExactRational, Statistics, StatValue
from app.edu.summary import build_summary

from .conftest import commit

LABELS = {
    "algorithm_data_structure": "算法与数据结构",
    "code_implementation": "代码实现能力",
    "problem_modeling": "问题建模能力",
    "efficiency_awareness": "效率意识",
    "solution_strategy": "策略与优化",
}


def value(number: float | None) -> StatValue:
    display = "-" if number is None else f"{number:.2f}"
    exact = None if number is None else ExactRational(numerator=1, denominator=1)
    return StatValue(value=number, exact=exact, display=display)


def dimension(code: str, *, mean: float | None, valid: int, missing: int = 0, invalid: int = 0):
    return DimensionStatistics(
        dimension_code=code,
        valid_count=valid,
        missing_count=missing,
        invalid_count=invalid,
        mean=value(mean),
        median=value(mean),
        minimum=value(None if mean is None else 1.0),
        maximum=value(None if mean is None else 5.0),
        distribution=(),
    )


def statistics(*dimensions: DimensionStatistics) -> Statistics:
    return Statistics(
        batch_id="synthetic-batch",
        snapshot_id="synthetic-snapshot",
        rule_version="edu.statistics.v1",
        scope={"kind": "batch", "record_count": 53},
        dimensions=dimensions,
    )


def test_summary_matches_published_statistics_and_repeats(client, runtime, stage):
    committed = commit(runtime, stage())
    url = f"/api/v1/edu/batches/{committed.batch_id}"
    response = client.get(url + "/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    published = client.get(url + "/statistics").json()
    assert body["batch_id"] == committed.batch_id
    assert body["rule_version"] == published["rule_version"]
    assert body["basis"]["student_count"] == 2
    assert body["basis"]["evaluation_count"] == 2 * len(published["dimensions"])
    assert body["basis"]["valid_count"] == sum(
        item["valid_count"] for item in published["dimensions"]
    )
    assert body["headline"].endswith("。") and "均值最低" in body["headline"]
    assert len(body["points"]) == len(published["dimensions"]) + 2
    assert client.get(url + "/summary").json() == body


def test_summary_without_any_valid_score_states_it_plainly():
    summary = build_summary(
        statistics(
            dimension("algorithm_data_structure", mean=None, valid=0, missing=2),
            dimension("code_implementation", mean=None, valid=0, invalid=2),
        ),
        student_count=2,
        labels=LABELS,
    )
    assert "没有有效评分" in summary.headline
    assert summary.basis.valid_count == 0
    assert summary.basis.evaluation_count == 4
    assert summary.points[-1].startswith("数据质量：缺失 2 份、非法 2 份")


def test_summary_with_one_scored_dimension_has_no_comparison_point():
    summary = build_summary(
        statistics(dimension("algorithm_data_structure", mean=3.04, valid=53)),
        student_count=53,
        labels=LABELS,
    )
    assert len(summary.points) == 2
    assert "相差" not in " ".join(summary.points)
    assert "算法与数据结构" in summary.headline


@pytest.mark.parametrize("missing,invalid", [(0, 0), (1, 1)])
def test_quality_point_never_hides_missing_or_invalid_scores(missing, invalid):
    summary = build_summary(
        statistics(
            dimension(
                "algorithm_data_structure",
                mean=3.04,
                valid=51,
                missing=missing,
                invalid=invalid,
            ),
            dimension("code_implementation", mean=3.62, valid=51),
        ),
        student_count=53,
        labels=LABELS,
    )
    quality = summary.points[-1]
    if missing or invalid:
        assert f"缺失 {missing} 份" in quality and f"非法 {invalid} 份" in quality
    else:
        assert "无缺失或非法评分" in quality
    assert "相差 0.58 分" in " ".join(summary.points)
