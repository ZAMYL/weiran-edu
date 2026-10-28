import { expect, it } from 'vitest';
import { discoverySummaryText } from './discovery-view';
it('formats score fractions and coefficients without changing count denominators', () => {
  expect(discoverySummaryText('有效53/53条；均值16917/5300，中位数331/100')).toBe(
    '有效评分53条（共53条）；均值3.19，中位数3.31',
  );
  expect(discoverySummaryText('均值差为-31/10分；目标10人，余组43人')).toBe(
    '均值差为-3.1分；目标10人，余组43人',
  );
  expect(discoverySummaryText('评分差：1/17条位于围栏之外')).toBe(
    '评分差：17 人中有 1 人位于围栏之外',
  );
  expect(discoverySummaryText('Spearman=-0.998877，相关不代表因果。')).toBe(
    '评分关联系数 -1，相关不代表因果。',
  );
});
it('formats explicit Fraction values but preserves actual group count denominators', () => {
  expect(
    discoverySummaryText('均值 Fraction(317, 105)，105/105 人，评分差 Fraction(-1, 1000)'),
  ).toBe('均值 3.02，105/105 人，评分差 0');
});
