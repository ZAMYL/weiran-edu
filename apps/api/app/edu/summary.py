"""Deterministic Chinese summary of one assessment batch.

Every sentence comes from the published statistics only: no model text, no new
threshold and no judgement about which score is acceptable. The same statistics
always produce the same summary, so pages and exported reports can share wording.
"""

from collections.abc import Mapping

from app.edu.analysis_models import DimensionStatistics, Statistics
from app.edu.models import Model


class SummaryBasis(Model):
    student_count: int
    evaluation_count: int
    valid_count: int
    missing_count: int
    invalid_count: int


class BatchSummary(Model):
    batch_id: str
    snapshot_id: str
    rule_version: str
    headline: str
    points: tuple[str, ...]
    caveats: tuple[str, ...]
    basis: SummaryBasis


def _counts(dimensions: tuple[DimensionStatistics, ...]) -> tuple[int, int, int]:
    return (
        sum(item.valid_count for item in dimensions),
        sum(item.missing_count for item in dimensions),
        sum(item.invalid_count for item in dimensions),
    )


def _dimension_point(item: DimensionStatistics, labels: Mapping[str, str]) -> str:
    name = labels.get(item.dimension_code, item.dimension_code)
    if item.mean.value is None:
        return f"{name}：本批没有有效评分，无法计算均值。"
    return (
        f"{name}：有效 {item.valid_count} 份，均值 {item.mean.display}，"
        f"中位数 {item.median.display}，最低 {item.minimum.display}、最高 {item.maximum.display}。"
    )


def _quality_point(missing: int, invalid: int, valid: int) -> str:
    if missing == 0 and invalid == 0:
        return f"数据质量：无缺失或非法评分，统计基于 {valid} 份有效评分。"
    return (
        f"数据质量：缺失 {missing} 份、非法 {invalid} 份，"
        f"统计基于 {valid} 份有效评分，缺失与非法评分不参与计算。"
    )


def build_summary(
    statistics: Statistics, *, student_count: int, labels: Mapping[str, str]
) -> BatchSummary:
    dimensions = statistics.dimensions
    valid, missing, invalid = _counts(dimensions)
    basis = SummaryBasis(
        student_count=student_count,
        evaluation_count=valid + missing + invalid,
        valid_count=valid,
        missing_count=missing,
        invalid_count=invalid,
    )
    scored = sorted(
        (item for item in dimensions if item.mean.value is not None),
        key=lambda item: item.mean.value or 0.0,
    )
    points = [_dimension_point(item, labels) for item in dimensions]
    caveats = [f"当前为规则统计（{statistics.rule_version}），未做显著性检验，只描述本批数据。"]
    if valid == 0:
        points.append(_quality_point(missing, invalid, valid))
        return BatchSummary(
            batch_id=statistics.batch_id,
            snapshot_id=statistics.snapshot_id,
            rule_version=statistics.rule_version,
            headline=f"本批 {student_count} 名学生没有有效评分，无法给出统计结论。",
            points=tuple(points),
            caveats=tuple(caveats),
            basis=basis,
        )
    lowest, highest = scored[0], scored[-1]
    low_name = labels.get(lowest.dimension_code, lowest.dimension_code)
    high_name = labels.get(highest.dimension_code, highest.dimension_code)
    headline = (
        f"{len(scored)} 个维度中{low_name}均值最低（{lowest.mean.display}），"
        f"{high_name}最高（{highest.mean.display}）。"
    )
    if len(scored) > 1:
        gap = highest.mean.value - lowest.mean.value
        points.append(f"维度间差距最大的是{low_name}与{high_name}，相差 {gap:.2f} 分。")
    points.append(_quality_point(missing, invalid, valid))
    return BatchSummary(
        batch_id=statistics.batch_id,
        snapshot_id=statistics.snapshot_id,
        rule_version=statistics.rule_version,
        headline=headline,
        points=tuple(points),
        caveats=tuple(caveats),
        basis=basis,
    )
