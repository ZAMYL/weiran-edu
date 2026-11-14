import type { AssessmentItem } from '../../services/edu-api/assessment-items';
import {
  describeItemStatistics,
  type AnalysisScope,
  type TeacherItemStatistics,
} from '../../services/edu-api/teacher-analysis';
import { dimensionName } from '../../services/edu-api/view-model';
import type { ApiRubric, ApiStatistics } from '../../services/edu-api/types';

export function ItemReading({
  item,
  statistics,
  scope,
}: {
  item: AssessmentItem;
  statistics: TeacherItemStatistics;
  scope: AnalysisScope;
}) {
  const copy = describeItemStatistics(statistics, item, scope);
  return (
    <p className="teacher-reading">
      {copy.finding} {copy.numbers}
    </p>
  );
}

export function ScoreReading({
  statistics,
  rubric,
  distribution,
  scopeCount,
}: {
  statistics: ApiStatistics;
  rubric: ApiRubric;
  distribution: boolean;
  scopeCount?: number;
}) {
  const scored = statistics.dimensions
    .filter((score) => score.valid_count > 0 && score.mean.value !== null)
    .sort((a, b) => b.mean.value! - a.mean.value!);
  const high = scored[0];
  const low = scored.at(-1);
  if (!high || !low) return <p className="teacher-reading">当前范围暂无可用评分。</p>;

  const names = (display: string) =>
    scored
      .filter((score) => score.mean.display === display)
      .map((score) => dimensionName(score.dimension_code))
      .join('、');
  const scope = { batchId: statistics.batch_id, snapshotId: statistics.snapshot_id };
  if (distribution) {
    return (
      <ItemReading
        item={{
          item_id: high.dimension_code,
          display_name: dimensionName(high.dimension_code),
          kind: 'competency',
          scale: { ...rubric.score_policy, unit: '分' },
        }}
        statistics={{ ...high, item_id: high.dimension_code }}
        scope={scope}
      />
    );
  }
  return (
    <p className="teacher-reading">
      {scopeCount === undefined
        ? `${statistics.scope.record_count} 人参与本次测评。`
        : `当前查看 ${scopeCount} 名学生的评分。`}
      {scored.length === 1
        ? `目前仅${names(high.mean.display)}有可用平均分，为 ${high.mean.display} 分。`
        : high.mean.display === low.mean.display
          ? `有有效评分的各项目平均分均为 ${high.mean.display} 分。可切到评分分布查看实得分数和人数。`
          : `平均分较高的是${names(high.mean.display)}（${high.mean.display} 分），较低的是${names(low.mean.display)}（${low.mean.display} 分）。`}
    </p>
  );
}
