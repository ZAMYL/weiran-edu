import { expect, it } from 'vitest';
import { metricDisplay } from './view-model';

it('shows integer headcounts even when the server presentation has decimal places', () => {
  expect(
    metricDisplay({
      unit: 'records',
      value: { value: 105, exact: { numerator: 105, denominator: 1 }, display: '105.00' },
    }),
  ).toBe('105');
});
it('formats score fractions and ratios without changing the original evidence values', () => {
  const value = {
    value: 317 / 105,
    exact: { numerator: 317, denominator: 105 },
    display: '317/105',
  };
  const original = structuredClone(value);
  expect(metricDisplay({ unit: 'score', value })).toBe('3.02');
  expect(value).toEqual(original);
  expect(
    metricDisplay({
      unit: 'ratio',
      value: { value: 1 / 3, exact: { numerator: 1, denominator: 3 }, display: '1/3' },
    }),
  ).toBe('33.33%');
  expect(
    metricDisplay({ unit: 'score', value: { value: null, exact: null, display: '数据不足' } }),
  ).toBe('—');
});
