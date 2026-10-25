import { DIMENSIONS, type ApiCondition, type ApiEvidence, type InsightMetric } from './types';

export const dimensionName = (code: string | null) =>
  DIMENSIONS.find((d) => d.code === code)?.name ?? '未识别维度';
export const operatorLabels = { lt: '<', lte: '≤', eq: '=', gte: '≥', gt: '>' } as const;
export const conditionText = (conditions: ApiCondition[]) =>
  conditions
    .map((c) => `${dimensionName(c.dimension_code)} ${operatorLabels[c.operator]} ${c.value}`)
    .join(' 且 ') || '全部学生';
export const metricDisplay = (metric: InsightMetric) =>
  metric.unit === 'ratio' && metric.value.value !== null
    ? `${(metric.value.value * 100).toFixed(2)}%`
    : metric.value.display;
export const commentStatusText = (status: ApiEvidence['comment_status']) =>
  status === 'published'
    ? '已审核展示文本 · 仅限授权教师'
    : status === 'withheld'
      ? '原始评语暂未开放（非空，待审核）'
      : '原始评语为空';
export function parseThreshold(value: string): number | null {
  const parts = /^(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!parts || (parts[2] ?? '').replace(/0+$/, '').length > 2) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 1 && number <= 5 ? number : null;
}
