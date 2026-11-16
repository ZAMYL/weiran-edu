import { readFileSync, writeFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { discoveryDetailDto } from './discovery-types';
import { count, list, literal, object, text, numeric } from './schema';
import { assessmentItemDto, statValueDto } from './assessment-items';
import type { AssessmentItem } from './assessment-items';
import type { ItemStatistics } from './types';
import {
  describeDiscovery,
  describeDiscoverySummary,
  describeItemStatistics,
  describeProfile,
  describeStudentScores,
  teacherAnalysisOverview,
  teacherNumber,
  type TeacherProfileInput,
} from './teacher-analysis';

const raw: unknown = JSON.parse(
  readFileSync(
    new URL('../../../tests/fixtures/teacher-analysis.synthetic.json', import.meta.url),
    'utf8',
  ),
);
const captured = object({
  synthetic_only: literal(true),
  cases: list(
    object({
      recipe: text,
      xlsx_sha256: text,
      students: count,
      retained_findings: count,
      details: list(discoveryDetailDto),
    }),
  ),
})(raw);
const itemCases = object({
  item_cases: list(
    object({
      origin: text,
      students: count,
      scope: object({ batchId: text, snapshotId: text }),
      items: list(assessmentItemDto),
      statistics: list(
        object({
          item_id: text,
          valid_count: count,
          missing_count: count,
          invalid_count: count,
          mean: statValueDto,
          distribution: list(object({ score: numeric, count })),
          evidence_ids: list(text),
        }),
      ),
    }),
  ),
})(raw).item_cases;
const cases = captured.cases;
const group = () => {
  const d = structuredClone(cases[0]!.details.find((d) => d.type === 'group_difference')!);
  if (d.type !== 'group_difference') throw new Error('Expected captured group detail');
  return d;
};
const fraction = (numerator: number, denominator = 1) => ({ numerator, denominator });
const exact = (numerator: number, denominator = 1) => ({
  ...fraction(numerator, denominator),
  display: (numerator / denominator).toFixed(4),
});
const stat = (numerator: number | null, denominator = 1) => ({
  value: numerator === null ? null : numerator / denominator,
  exact: numerator === null ? null : fraction(numerator, denominator),
  display: numerator === null ? '数据不足' : String(numerator / denominator),
});

it('explains two actual kernel outputs with their own counts and scores, without mutating data', () => {
  expect(cases.map((c) => c.students)).toEqual([72, 96]);
  const texts = cases.map((example) => {
    const before = JSON.stringify(example);
    const narratives = example.details.map((d) => {
      const copy = describeDiscovery(d);
      expect(copy.id).toBe(d.discovery_id);
      expect(copy.source.batchId).toBe(d.batch_id);
      expect(copy.source.snapshotId).toBe(d.snapshot_id);
      expect(copy.finding).not.toMatch(/关联系数|维度关联|互斥|反向变化|置信度/);
      expect(copy.nextCheck).not.toBe('');
      if (d.type === 'group_difference') {
        const row = d.chart.find((r) => r.dimension_code === d.metrics.outcome_dimension)!;
        expect(copy.numbers).toContain(`${d.metrics.target_count} 人`);
        expect(copy.numbers).toContain(`${d.metrics.remainder_count} 人`);
        expect(copy.numbers).toContain(teacherNumber(row.target.mean!));
        expect(copy.numbers).toContain(teacherNumber(row.remainder.mean!));
        if (!row.condition_defined && BigInt(row.difference!.numerator) !== BigInt(0)) {
          const overall = d.scope.statistics.dimensions.find(
            (s) => s.dimension_code === d.metrics.outcome_dimension,
          )!.mean.exact!;
          expect(copy.whyItMatters).toContain(teacherNumber(overall));
          expect(copy.whyItMatters).toContain('单看这一均分不容易看到');
        }
      }
      return copy;
    });
    expect(JSON.stringify(example)).toBe(before);
    return narratives;
  });
  expect(texts[0]).not.toEqual(texts[1]);
  for (const example of cases) {
    const groups = example.details.filter((d) => d.type === 'group_difference');
    expect(groups).toHaveLength(2);
    expect(new Set(groups.map((d) => describeDiscovery(d).title)).size).toBe(2);
  }
});

it('uses actual two-item and seven-item offline Kernel outputs rather than a fixed five-item story', () => {
  expect(itemCases.map((c) => c.items.length)).toEqual([2, 7]);
  expect(itemCases.map((c) => c.students)).toEqual([14, 27]);
  for (const c of itemCases) {
    const before = JSON.stringify(c);
    const copies = c.statistics.map((stats) => {
      const copy = describeItemStatistics(
        stats,
        c.items.find((item) => item.item_id === stats.item_id)!,
        c.scope,
      );
      const first = stats.distribution[0]!;
      const last = stats.distribution.at(-1)!;
      expect(copy.numbers).toContain(`${first.score}`);
      expect(copy.numbers).toContain(`${first.count} 人`);
      expect(copy.numbers).toContain(`${last.score}`);
      expect(copy.numbers).toContain(`${last.count} 人`);
      expect(copy.source.evidenceIds).toEqual(stats.evidence_ids);
      expect(copy.whyItMatters).toContain('不能把平均分当作每名学生的得分');
      return copy;
    });
    expect(copies).toHaveLength(c.items.length);
    expect(JSON.stringify(c)).toBe(before);
  }
});

it('can regenerate teacher examples from the captured data, with an explicit distinction between formal and offline results', () => {
  if (process.env.EDU_WRITE_TEACHER_EXAMPLES !== '1') return;
  const lines = [
    '# 教师解释实测示例',
    '',
    '全部为合成数据；文字由当前纯函数根据已捕获结果生成，不是人工填写的产品文案。',
    '',
    '## 正式发现内核：整体均分之外的分组线索',
    '',
  ];
  for (const c of cases) {
    lines.push(
      `### ${c.recipe}（${c.students} 人）`,
      '',
      `原内核保留 ${c.retained_findings} 条发现；以下仅展示原顺序中的部分条目。`,
      '',
    );
    for (const d of c.details.filter((d) => d.type === 'group_difference').slice(0, 2)) {
      const copy = describeDiscovery(d);
      lines.push(
        `**${copy.title}**`,
        '',
        copy.finding,
        '',
        copy.numbers,
        '',
        copy.whyItMatters,
        '',
        copy.nextCheck,
        '',
        copy.limitation,
        '',
        `来源：discovery_id=${copy.id}，snapshot_id=${copy.source.snapshotId}。`,
        '',
      );
    }
  }
  lines.push(
    '## 非五维结构：现有离线逐项目 Kernel',
    '',
    '以下是已有 canonical Kernel 的真实合成执行输出，不代表 release 已开放逐题 AI 或对应 HTTP 调查能力。这里只解释已有分布与缺失计数，没有生成群体发现。',
    '',
  );
  for (const c of itemCases) {
    lines.push(`### ${c.students} 人、${c.items.length} 个逐题项目`, '');
    for (const stats of c.statistics.slice(0, 2)) {
      const copy = describeItemStatistics(
        stats,
        c.items.find((item) => item.item_id === stats.item_id)!,
        c.scope,
      );
      lines.push(
        `**${copy.title}**`,
        '',
        copy.finding,
        '',
        copy.numbers,
        '',
        copy.whyItMatters,
        '',
        copy.nextCheck,
        '',
      );
    }
  }
  writeFileSync(
    new URL('../../../../../docs/TEACHER_ANALYSIS_EXAMPLES.md', import.meta.url),
    lines.join('\n'),
  );
});

it('separates group headcount from valid-score denominators and keeps the supplied comparison direction', () => {
  const d = group();
  const row = d.chart.find((r) => r.dimension_code === d.metrics.outcome_dimension)!;
  row.target.valid_count = 7;
  row.target.mean = exact(2);
  row.remainder.mean = exact(3);
  row.difference = exact(-1);
  d.metrics.unevaluable_count = 4;
  const copy = describeDiscovery(d);
  expect(copy.title).toContain('平均分更低');
  expect(copy.numbers).toContain(`符合条件的 ${d.metrics.target_count} 人中，7 人`);
  expect(copy.numbers).toContain('4 人因分组评分不完整或无效');
  expect(copy.limitation).toContain('不能直接说明');
  row.condition_defined = true;
  expect(describeDiscovery(d).whyItMatters).toContain('不能作为另一条新发现');
});

it('does not describe missing group means as a real difference', () => {
  const d = group();
  d.chart.find((r) => r.dimension_code === d.metrics.outcome_dimension)!.target.mean = null;
  const copy = describeDiscovery(d);
  expect(copy.state).toBe('insufficient_data');
  expect(copy.numbers).toBe('');
  expect(copy.title).not.toMatch(/更高|更低/);
});

it.each([0.8, -0.8, 0, null])(
  'explains paired scores for rho=%s without claiming a cause',
  (rho) => {
    const d = discoveryDetailDto({
      ...group(),
      type: 'dimension_relation',
      conditions: [],
      chart: [],
      metrics: {
        left: 'code_implementation',
        right: 'problem_modeling',
        rho: rho === null ? null : { value: rho, method: 'stdlib_float' },
        paired_count: 19,
        excluded_count: 3,
        student_denominator: 22,
      },
    });
    const copy = describeDiscovery(d);
    expect(copy.numbers).toBe('本次比较使用 19 名学生的两项有效评分，3 人未参与比较。');
    expect(copy.limitation).toContain('不能据此判断一项影响了另一项');
    if (rho === null) expect(copy.state).toBe('insufficient_data');
    else
      expect(copy.title).toContain(
        rho > 0 ? '一起较高或较低' : rho < 0 ? '另一项往往较低' : '未显示同升同降',
      );
  },
);

it('keeps low/high unusual score differences distinct without diagnosing errors', () => {
  const make = (side: 'lower' | 'upper') =>
    discoveryDetailDto({
      ...group(),
      type: 'rare_combination',
      conditions: [],
      chart: [],
      metrics: {
        left: 'algorithm_data_structure',
        right: 'code_implementation',
        pair_count: 22,
        excluded_count: 3,
        match_count: 2,
        pattern: 'small_group_review',
        share_pairs: exact(1, 11),
        q1: exact(0),
        q3: exact(1),
        lower_fence: exact(-2),
        upper_fence: exact(3),
        side,
        student_denominator: 25,
      },
    });
  const low = describeDiscovery(make('lower')),
    high = describeDiscovery(make('upper'));
  expect(low.title).not.toBe(high.title);
  expect(low.numbers).toContain('低于 -2 分');
  expect(high.numbers).toContain('高于 3 分');
  expect(low.limitation).toContain('不说明评分有误');
});

it('describes boundary counts as a choice of filtering, without a new teaching finding', () => {
  const d = discoveryDetailDto({
    ...group(),
    type: 'boundary_sensitivity',
    chart: [
      { operator: 'lt', count: 12 },
      { operator: 'lte', count: 18 },
    ],
    metrics: {
      strict_count: 12,
      inclusive_count: 18,
      added_count: 6,
      removed_count: 0,
      student_denominator: 25,
      eligible_denominator: 22,
      unevaluable_count: 3,
      share_strict_total: exact(12, 25),
      share_inclusive_total: exact(18, 25),
      share_strict_eligible: exact(12, 22),
      share_inclusive_eligible: exact(18, 22),
    },
  });
  expect(describeDiscovery(d).numbers).toContain('12 人，计入边界分数后有 18 人，增加 6 人');
  expect(describeDiscovery(d).limitation).toContain('不是新增的教学结论');
});

it('keeps every finding and its server order, with two or three highlights only', () => {
  const ordered = [...cases[0]!.details, ...cases[1]!.details];
  const before = ordered.map((d) => d.discovery_id);
  for (const limit of [2, 3] as const) {
    const view = teacherAnalysisOverview(ordered, limit);
    expect(view.highlights).toEqual(ordered.slice(0, limit));
    expect([...view.highlights, ...view.remaining]).toEqual(ordered);
    expect(view.all.map((d) => d.discovery_id)).toEqual(before);
  }
  expect(teacherAnalysisOverview([]).all).toEqual([]);
});

it('uses the original summary title and never invents detail numbers while loading', () => {
  const d = group();
  const copy = describeDiscoverySummary(d);
  expect(copy.title).toBe(d.title);
  expect(copy.state).toBe('needs_detail');
  expect(copy.numbers).toBe('');
});

it('supports variable item counts, names and scales for actual item statistics', () => {
  for (const size of [2, 7]) {
    const items: AssessmentItem[] = Array.from({ length: size }, (_, i) => ({
      item_id: `Q${i + 1}`,
      display_name: `课程项目${i + 1}`,
      kind: 'question',
      scale: { minimum: 0, maximum: 100, step: 1, unit: '分' },
    }));
    const copies = items.map((item) => {
      const stats: ItemStatistics = {
        item_id: item.item_id,
        valid_count: 17,
        missing_count: 2,
        invalid_count: 1,
        mean: stat(725, 10),
        median: stat(70),
        minimum: stat(0),
        maximum: stat(100),
        distribution: [],
        evidence_ids: ['evidence-synthetic'],
      };
      return describeItemStatistics(stats, item, { batchId: 'b', snapshotId: 's' });
    });
    expect(copies).toHaveLength(size);
    expect(copies.at(-1)!.title).toContain(`课程项目${size}`);
    expect(copies[0]!.finding).toContain('72.5 分');
    expect(copies[0]!.numbers).toContain('缺少评分 2 人，评分无效 1 人');
    expect(copies[0]!.numbers).toContain('0～100 分');
    expect(copies[0]!.source.evidenceIds).toEqual(['evidence-synthetic']);
  }
});

it('does not invent a mean or a scale, and fails mismatched item wiring', () => {
  const item: AssessmentItem = {
    item_id: 'Q',
    display_name: '实验记录',
    kind: 'question',
    scale: null,
  };
  const stats: ItemStatistics = {
    item_id: 'Q',
    valid_count: 0,
    missing_count: 3,
    invalid_count: 0,
    mean: stat(null),
    median: stat(null),
    minimum: stat(null),
    maximum: stat(null),
    distribution: [],
    evidence_ids: undefined,
  };
  const copy = describeItemStatistics(stats, item, { batchId: 'b', snapshotId: 's' });
  expect(copy.state).toBe('insufficient_data');
  expect(copy.numbers).toContain('未提供本项目量表');
  expect(copy.finding).not.toContain('0.00');
  expect(() =>
    describeItemStatistics({ ...stats, item_id: 'other' }, item, { batchId: 'b', snapshotId: 's' }),
  ).toThrow('mismatch');
});

it('describes verified profiles of different sizes, preserves Fact IDs, and does not promote grouping items', () => {
  const profile: TeacherProfileInput = {
    profile_id: 'p',
    batch_id: 'b',
    snapshot_id: 's',
    source_replay_verified: true,
    verified_fact_ids: ['fact-real'],
    conditions: [{ dimension_code: 'Q1', operator: '<=', value: '4' }],
    dimensions: Array.from({ length: 2 }, (_, i) => ({
      dimension_code: `Q${i + 1}`,
      label: `项目${i + 1}`,
      condition_defined: i === 0,
      target: { valid_count: 11, mean: exact(325, 100) },
      remainder: { valid_count: 9, mean: exact(275, 100) },
    })),
  };
  const copies = describeProfile(profile);
  expect(copies).toHaveLength(2);
  expect(copies[0]!.whyItMatters).toContain('本身用于分组');
  expect(copies[1]!.numbers).toContain('本组 11 人');
  expect(copies[1]!.numbers).toContain('3.25 分');
  expect(copies[1]!.source.factIds).toEqual(['fact-real']);
  expect(describeProfile({ ...profile, source_replay_verified: false })).toEqual([]);
  const empty = structuredClone(profile);
  empty.dimensions[0]!.target.valid_count = 0;
  expect(describeProfile(empty)[0]!.numbers).toContain('暂不比较均分');
});

it('uses only supplied student scores and evidence, without scoring missing items', () => {
  const items: AssessmentItem[] = [
    {
      item_id: 'Q1',
      display_name: '实验记录',
      kind: 'question',
      scale: { minimum: 0, maximum: 10, step: 1, unit: undefined },
    },
  ];
  const copy = describeStudentScores(
    {
      student_id: 'anonymous',
      display_label: '匿名学生A',
      scores: [{ item_id: 'Q1', score: exact(8), evidence_id: 'e-1' }],
    },
    items,
    { batchId: 'b', snapshotId: 's' },
  );
  expect(copy.numbers).toBe('实验记录 8 分');
  expect(copy.source.evidenceIds).toEqual(['e-1']);
  expect(
    describeStudentScores({ student_id: 's', display_label: '学生', scores: [] }, items, {
      batchId: 'b',
      snapshotId: 's',
    }).state,
  ).toBe('insufficient_data');
});

it('rounds supplied exact scores HALF_UP, including negative ties, huge strings and negative zero', () => {
  expect(teacherNumber(exact(40250, 10000))).toBe('4.03');
  expect(teacherNumber(exact(20000, 10000))).toBe('2');
  expect(teacherNumber(exact(1005, 1000))).toBe('1.01');
  expect(teacherNumber(exact(-1005, 1000))).toBe('-1.01');
  expect(teacherNumber(exact(-1, 10000))).toBe('0');
  expect(
    teacherNumber({ numerator: '900719925474099300', denominator: '300239975158033100' }),
  ).toBe('3');
  expect(() => teacherNumber({ numerator: 1, denominator: 0 })).toThrow();
});
