import { expect, it } from 'vitest';
import type { ApiCondition } from './types';
import { explorerConditions } from './discovery-navigation';

const scope: ApiCondition[] = [
  { dimension_code: 'algorithm_data_structure', operator: 'lte', value: 3 },
];
const condition = (
  dimension_code: 'code_implementation' | 'algorithm_data_structure' | 'problem_modeling',
  numerator: number,
  denominator = 1,
  display = String(numerator / denominator),
) => ({
  dimension_code,
  operator: 'gte' as const,
  threshold: { numerator, denominator, display },
});
it('carries the original scoped condition into the explorer, preserving values without aliasing', () => {
  const next = explorerConditions(scope, { conditions: [] });
  expect(next).toEqual(scope);
  expect(next).not.toBe(scope);
  next![0]!.value = 5;
  expect(scope[0]!.value).toBe(3);
});
it('joins a legal discovery filter to its outer scope without losing either', () => {
  expect(explorerConditions(scope, { conditions: [condition('code_implementation', 275, 100, '2.75')] }))
    .toEqual([...scope, { dimension_code: 'code_implementation', operator: 'gte', value: 2.75 }]);
});
it('does not merge duplicate dimension predicates or more than two predicates', () => {
  expect(explorerConditions(scope, { conditions: [condition('algorithm_data_structure', 2)] }))
    .toBeNull();
  expect(explorerConditions(scope, {
    conditions: [condition('code_implementation', 3), condition('problem_modeling', 4)],
  })).toBeNull();
});
it('rejects unrepresentable off-grid or inaccurate display values instead of rounding', () => {
  expect(explorerConditions([], { conditions: [condition('code_implementation', 2751, 1000, '2.751')] }))
    .toBeNull();
  expect(explorerConditions([], { conditions: [condition('code_implementation', 11, 4, '2.76')] }))
    .toBeNull();
});
