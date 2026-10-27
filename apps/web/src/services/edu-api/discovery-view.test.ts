import { expect, it } from 'vitest';
import { discoverySummaryText } from './discovery-view';
it('formats score fractions and coefficients without changing count denominators', () => {
  expect(discoverySummaryText('均值差为-31/10分；目标10人，余组43人')).toBe(
    '均值差为-3.10分；目标10人，余组43人',
  );
  expect(discoverySummaryText('评分差：1/17条位于围栏之外')).toBe(
    '评分差：17 人中有 1 人位于围栏之外',
  );
  expect(discoverySummaryText('Spearman=-0.998877，相关不代表因果。')).toBe(
    '评分关联系数 -1.00，相关不代表因果。',
  );
});
