import { expect, it } from 'vitest';
import { discoveryDetailDto } from './discovery-types';
import { DIMENSIONS } from './types';
import {
  discoveryConditionsText,
  discoveryEvidenceNotes,
  discoveryPresentation,
  eligibleForFocus,
} from './discovery-presentation';

const exact = (numerator: number, denominator = 1) => ({
  numerator,
  denominator,
  display: (numerator / denominator).toFixed(4),
});
function group() {
  const stat = { value: 3, exact: exact(3), display: '3' };
  const stats = (count: number, mean: ReturnType<typeof exact>) => ({
    valid_count: count,
    missing_count: 0,
    invalid_count: 0,
    mean,
    median: mean,
    distribution: [],
  });
  const d = discoveryDetailDto({
    discovery_id: 'discovery',
    batch_id: 'batch',
    snapshot_id: 'snapshot',
    input_key: 'input',
    rule_version: 'version',
    rule_id: 'group',
    type: 'group_difference',
    title: '互斥群体的代码实现能力评分差异',
    summary: '目标组15人，余组38人',
    status: 'interpret_cautiously',
    public_display_allowed: false,
    scope: {
      batch_id: 'batch',
      snapshot_id: 'snapshot',
      input_key: 'input',
      profile: 'edu.discovery.profile.v1',
      scope_conditions: [],
      batch_record_count: 53,
      scope_record_count: 53,
      eligible_count: 53,
      unevaluable_count: 0,
      public_display_allowed: false,
      statistics: {
        batch_id: 'batch',
        snapshot_id: 'snapshot',
        rule_version: 'version',
        scope: { kind: 'batch', record_count: 53 },
        display_policy: { digits: 2, rounding: 'HALF_UP' },
        matrix: null,
        dimensions: DIMENSIONS.map((d) => ({
          dimension_code: d.code,
          valid_count: 53,
          missing_count: 0,
          invalid_count: 0,
          mean: stat,
          median: stat,
          minimum: stat,
          maximum: stat,
          distribution: [],
        })),
      },
    },
    conditions: [
      { dimension_code: 'algorithm_data_structure', operator: 'lte', threshold: exact(349, 100) },
    ],
    limitations: ['余组排除条件不可判定者；筛选维度差异属于条件定义，不作为新发现。'],
    controlled_queries: [{ discovery_id: 'discovery', metric_key: 'target_members' }],
    parameters: {
      minimum_samples: 5,
      support_target: 20,
      search_budget: 20,
      maximum_findings: 12,
      maximum_students: 5000,
      correlation_threshold: exact(3, 5),
      mean_difference: exact(1, 2),
      rare_share: exact(1, 10),
      minimum_contrast: exact(1, 2),
    },
    ranking: {
      score: exact(1),
      effect: exact(69, 100),
      support_count: 15,
      support: exact(15, 20),
      completeness: exact(1),
      robustness: {
        method: 'three_fixed_deletions',
        planned: 3,
        evaluated: 3,
        passed: 3,
        value: exact(1),
      },
      is_statistical_confidence: false,
    },
    metrics: {
      outcome_dimension: 'code_implementation',
      target_count: 15,
      remainder_count: 38,
      unevaluable_count: 0,
      student_denominator: 53,
      eligible_denominator: 53,
      share_total: exact(15, 53),
      share_eligible: exact(15, 53),
    },
    chart: [
      {
        dimension_code: 'code_implementation',
        condition_defined: false,
        target: stats(15, exact(369, 100)),
        remainder: stats(38, exact(3)),
        difference: exact(69, 100),
      },
    ],
  });
  if (d.type !== 'group_difference') throw new Error('Invalid synthetic group fixture');
  return d;
}

it('expresses the real 53 / 15 / 38 comparison with 3.69, 3 and +0.69, without technical titles', () => {
  const d = group();
  const before = JSON.stringify(d);
  const p = discoveryPresentation(d);
  expect(p.title).toBe('算法与数据结构评分不高于 3.49 分：代码实现能力平均分更高');
  expect(p.grouping).toBe('分组依据：算法与数据结构 ≤ 3.49 分');
  expect(p.metrics.map((m) => m.value)).toEqual(['3.69', '3.00', '+0.69']);
  expect(p.metrics.map((m) => m.label)).toEqual([
    '符合条件的学生 · 15 人',
    '其他可判定学生 · 38 人',
    '平均分相差',
  ]);
  expect(p.explanation).toContain('15 人中，15 人的有效评分平均为 3.69 分');
  expect(p.explanation).toContain('另外 38 人中，38 人的有效评分平均为 3 分');
  expect(p.explanation).not.toMatch(/目标组|余组|互斥/);
  expect(eligibleForFocus(d)).toBe(true);
  expect(JSON.stringify(d)).toBe(before);
});

it('preserves the original group orientation and AND conditions when the difference is negative', () => {
  const d = group();
  d.chart[0]!.target.mean = exact(3);
  d.chart[0]!.remainder.mean = exact(369, 100);
  d.chart[0]!.difference = exact(-69, 100);
  d.conditions.push({
    dimension_code: 'solution_strategy',
    operator: 'gte',
    threshold: exact(325, 100),
  });
  const p = discoveryPresentation(d);
  expect(p.title).toContain('平均分更低');
  expect(p.metrics[2]?.value).toBe('-0.69');
  expect(discoveryConditionsText(d.conditions)).toBe(
    '算法与数据结构 ≤ 3.49 分 且 解题策略 ≥ 3.25 分',
  );
  expect(d.metrics.target_count).toBe(15);
});

it('does not promote explicit insufficient evidence, low support, missing means or condition-defined outcomes', () => {
  const d = group();
  d.status = 'insufficient_evidence';
  expect(eligibleForFocus(d)).toBe(false);
  d.status = 'displayable';
  d.ranking.support_count = 4;
  expect(eligibleForFocus(d)).toBe(false);
  d.ranking.support_count = 15;
  d.chart[0]!.target.mean = null;
  expect(eligibleForFocus(d)).toBe(false);
  expect(discoveryPresentation(d).metrics[0]?.value).toBe('—');
  d.chart[0]!.target.mean = exact(369, 100);
  d.chart[0]!.condition_defined = true;
  expect(eligibleForFocus(d)).toBe(false);
});

it('keeps group headcounts separate from valid-score denominators and explains actual missing data and stability', () => {
  const d = group();
  d.metrics.unevaluable_count = 2;
  d.chart[0]!.target.valid_count = 14;
  d.chart[0]!.target.missing_count = 1;
  d.ranking.robustness.evaluated = 2;
  d.ranking.robustness.passed = 1;
  const p = discoveryPresentation(d);
  expect(p.metrics[0]?.label).toContain('15 人');
  expect(p.metrics[0]?.note).toBe('有效评分 14 人');
  const notes = discoveryEvidenceNotes(d).join('\n');
  expect(notes).toContain('未进入比较：2 人');
  expect(notes).toContain('缺失 1 条');
  expect(notes).toContain('计划 3 次，完成 2 次，符合检查规则 1 次');
  expect(notes).toContain('不是统计置信度');
  expect(notes).toContain('结果稳定性有限');
  expect(notes).not.toMatch(/目标组|余组|互斥/);
});

it('formats fractional means consistently with HALF_UP display and never displays negative zero', () => {
  const d = group();
  d.chart[0]!.target.mean = exact(2995, 1000);
  d.chart[0]!.difference = exact(-1, 10000);
  expect(discoveryPresentation(d).metrics.map((m) => m.value)).toEqual(['3.00', '3.00', '0.00']);
});

it('describes positive and negative correlation without asserting causality', () => {
  const make = (rho: number | null) =>
    discoveryDetailDto({
      ...group(),
      type: 'dimension_relation',
      conditions: [],
      metrics: {
        left: 'algorithm_data_structure',
        right: 'code_implementation',
        rho: rho === null ? null : { value: rho, method: 'stdlib_float' },
        paired_count: 51,
        excluded_count: 2,
        student_denominator: 53,
      },
      chart: [],
    });
  expect(discoveryPresentation(make(0.8)).title).toContain('一起较高或较低');
  expect(discoveryPresentation(make(-0.8)).title).toContain('另一项往往较低');
  expect(discoveryPresentation(make(0.8)).explanation).toContain('不能据此判断一项影响了另一项');
  expect(eligibleForFocus(make(null))).toBe(false);
});

it('keeps individual rare combinations out of priority discoveries without inventing ability defects', () => {
  const make = (count: number) =>
    discoveryDetailDto({
      ...group(),
      type: 'rare_combination',
      conditions: [],
      metrics: {
        left: 'algorithm_data_structure',
        right: 'code_implementation',
        pair_count: 53,
        excluded_count: 0,
        match_count: count,
        pattern: count === 1 ? 'individual_review' : 'group_pattern',
        share_pairs: exact(count, 53),
        q1: exact(0),
        q3: exact(1),
        lower_fence: exact(-1),
        upper_fence: exact(2),
        side: 'upper',
        student_denominator: 53,
      },
      chart: [],
    });
  expect(eligibleForFocus(make(1))).toBe(false);
  expect(discoveryEvidenceNotes(make(1)).join('')).toContain('依据不足');
  const many = make(6);
  expect(eligibleForFocus(many)).toBe(true);
  expect(discoveryPresentation(many).explanation).toContain('不说明评分有误');
});

it('explains boundary changes as filtering rather than a teaching conclusion', () => {
  const d = discoveryDetailDto({
    ...group(),
    type: 'boundary_sensitivity',
    metrics: {
      strict_count: 10,
      inclusive_count: 15,
      added_count: 5,
      removed_count: 0,
      student_denominator: 53,
      eligible_denominator: 53,
      unevaluable_count: 0,
      share_strict_total: exact(10, 53),
      share_inclusive_total: exact(15, 53),
      share_strict_eligible: exact(10, 53),
      share_inclusive_eligible: exact(15, 53),
    },
    chart: [
      { operator: 'lt', count: 10 },
      { operator: 'lte', count: 15 },
    ],
  });
  expect(discoveryPresentation(d).title).toContain('增加 5 人');
  expect(discoveryPresentation(d).explanation).toContain('不是新增的教学结论');
  expect(discoveryPresentation(d).metrics.map((m) => m.value)).toEqual(['10', '15', '5']);
});
