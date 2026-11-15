import type { DiscoveryDetail, DiscoveryExact } from './discovery-types';
import { exactToNumber as value } from './exact';
import { dimensionName, operatorLabels, ratioDisplay, scoreDisplay } from './view-model';
import { describeDiscovery } from './teacher-analysis';

const score = (v: DiscoveryExact | null) =>
  v === null
    ? '—'
    : new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(
        Math.abs(Number(v.display)) < 0.005 ? 0 : Number(v.display),
      );
export const discoveryConditionsText = (conditions: DiscoveryDetail['conditions']) =>
  conditions
    .map(
      (c) =>
        `${dimensionName(c.dimension_code)} ${operatorLabels[c.operator]} ${scoreDisplay(value(c.threshold))} 分`,
    )
    .join(' 且 ');

export interface FindingMetric {
  label: string;
  value: string;
  unit?: string;
  note?: string;
}

// Presentation only: counts, means, differences, eligibility and original queries stay server-owned.
export function eligibleForFocus(d: DiscoveryDetail): boolean {
  if (
    d.status === 'insufficient_evidence' ||
    d.ranking.support_count < d.parameters.minimum_samples
  )
    return false;
  switch (d.type) {
    case 'group_difference': {
      const row = d.chart.find((r) => r.dimension_code === d.metrics.outcome_dimension);
      return Boolean(
        d.conditions.length &&
        row &&
        !row.condition_defined &&
        row.target.mean &&
        row.remainder.mean &&
        row.difference &&
        Math.min(row.target.valid_count, row.remainder.valid_count) >= d.parameters.minimum_samples,
      );
    }
    case 'dimension_relation':
      return d.metrics.rho !== null && d.metrics.paired_count >= d.parameters.minimum_samples;
    case 'rare_combination':
      return (
        d.metrics.pattern === 'group_pattern' &&
        d.metrics.match_count >= d.parameters.minimum_samples
      );
    case 'boundary_sensitivity':
      return d.metrics.eligible_denominator >= d.parameters.minimum_samples;
  }
}

export function discoveryPresentation(d: DiscoveryDetail): {
  title: string;
  grouping: string;
  explanation: string;
  metrics: FindingMetric[];
  primary: number;
} {
  const copy = describeDiscovery(d);
  const explanation = [
    copy.finding,
    copy.numbers,
    copy.whyItMatters,
    copy.nextCheck,
    copy.limitation,
  ]
    .filter(Boolean)
    .join(' ');
  switch (d.type) {
    case 'group_difference': {
      const row = d.chart.find((r) => r.dimension_code === d.metrics.outcome_dimension);
      const delta = row?.difference ? value(row.difference) : null;
      return {
        title: copy.title,
        grouping: `分组依据：${discoveryConditionsText(d.conditions) || '未提供分组条件'}`,
        explanation,
        metrics: [
          {
            label: `符合条件的学生 · ${d.metrics.target_count} 人`,
            value: score(row?.target.mean ?? null),
            unit: '分',
            note:
              row && row.target.valid_count !== d.metrics.target_count
                ? `有效评分 ${row.target.valid_count} 人`
                : undefined,
          },
          {
            label: `其他可判定学生 · ${d.metrics.remainder_count} 人`,
            value: score(row?.remainder.mean ?? null),
            unit: '分',
            note:
              row && row.remainder.valid_count !== d.metrics.remainder_count
                ? `有效评分 ${row.remainder.valid_count} 人`
                : undefined,
          },
          {
            label: '平均分相差',
            value: row?.difference ? `${delta! > 0 ? '+' : ''}${score(row.difference)}` : '—',
            unit: '分',
            note: '符合条件减其他学生',
          },
        ],
        primary: 2,
      };
    }
    case 'dimension_relation':
      return {
        title: copy.title,
        grouping: `比较项目：${dimensionName(d.metrics.left)}、${dimensionName(d.metrics.right)}`,
        explanation,
        metrics: [
          {
            label: '两项评分对应程度',
            value: d.metrics.rho ? scoreDisplay(d.metrics.rho.value) : '—',
          },
          { label: '两项评分均有效', value: String(d.metrics.paired_count), unit: '人' },
          { label: '未参与配对', value: String(d.metrics.excluded_count), unit: '人' },
        ],
        primary: 0,
      };
    case 'rare_combination':
      return {
        title: copy.title,
        grouping: `比较项目：${dimensionName(d.metrics.left)}、${dimensionName(d.metrics.right)}`,
        explanation,
        metrics: [
          { label: '出现这类组合', value: String(d.metrics.match_count), unit: '人' },
          { label: '两项评分均有效', value: String(d.metrics.pair_count), unit: '人' },
          { label: '占有效配对学生', value: ratioDisplay(value(d.metrics.share_pairs)) },
        ],
        primary: 0,
      };
    case 'boundary_sensitivity':
      return {
        title: copy.title,
        grouping: `筛选依据：${discoveryConditionsText(d.conditions) || '当前评分边界'}`,
        explanation,
        metrics: [
          { label: '不含边界分数', value: String(d.metrics.strict_count), unit: '人' },
          { label: '包含边界分数', value: String(d.metrics.inclusive_count), unit: '人' },
          { label: '新增符合筛选', value: String(d.metrics.added_count), unit: '人' },
        ],
        primary: 2,
      };
  }
}

export function discoveryEvidenceNotes(d: DiscoveryDetail): string[] {
  const notes = [
    `支持样本 ${d.ranking.support_count} 人；当前规则要求至少 ${d.parameters.minimum_samples} 人。`,
  ];
  if (!eligibleForFocus(d)) notes.push('当前依据不足以列为重点发现，请在此核查样本与评分记录。');
  switch (d.type) {
    case 'group_difference': {
      const row = d.chart.find((r) => r.dimension_code === d.metrics.outcome_dimension);
      notes.push(`因分组条件不可判定而未进入比较：${d.metrics.unevaluable_count} 人。`);
      if (row)
        notes.push(
          `比较项目有效评分：符合条件 ${row.target.valid_count} 人，其他可判定学生 ${row.remainder.valid_count} 人；缺失 ${row.target.missing_count + row.remainder.missing_count} 条，无效 ${row.target.invalid_count + row.remainder.invalid_count} 条。`,
        );
      break;
    }
    case 'dimension_relation':
    case 'rare_combination':
      notes.push(`缺少完整有效配对而未参与比较：${d.metrics.excluded_count} 人。`);
      break;
    case 'boundary_sensitivity':
      notes.push(`因筛选条件不可判定而未参与比较：${d.metrics.unevaluable_count} 人。`);
  }
  const r = d.ranking.robustness;
  notes.push(
    `服务端稳定性检查：计划 ${r.planned} 次，完成 ${r.evaluated} 次，符合检查规则 ${r.passed} 次。未完成的检查不视为通过；该结果不是统计置信度。`,
  );
  if (r.evaluated < r.planned || r.passed < r.evaluated)
    notes.push('部分检查未完成或未符合规则，结果稳定性有限，请结合具体评分依据判断。');
  return [
    ...notes,
    ...d.limitations.map((s) =>
      s
        .replace(/目标组/g, '符合条件的学生')
        .replace(/余组/g, '其他可判定学生')
        .replace(/互斥群体/g, '两组学生'),
    ),
  ];
}
