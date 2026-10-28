import { expect, it } from 'vitest';
import { metricDisplay, scoreDisplay, ratioDisplay } from './view-model';
import { commitDto } from './types';

it('shows integer headcounts even when the server presentation has decimal places', () => {
  expect(
    metricDisplay({
      unit: 'records',
      value: { value: 105, exact: { numerator: 105, denominator: 1 }, display: '105.00' },
    }),
  ).toBe('105');
});
it('uses concise numbers and never displays negative zero', () => {
  expect(scoreDisplay(-0.0001)).toBe('0');
  expect(scoreDisplay(3)).toBe('3');
  expect(scoreDisplay(3.125)).toBe('3.13');
  expect(ratioDisplay(1)).toBe('100%');
  expect(ratioDisplay(null)).toBe('—');
});
it('keeps the commit snapshot reference and rejects malformed handoff values', () => {
  const receipt = {
    import_id: 'import',
    batch_id: 'batch',
    snapshot_id: 'snapshot',
    result: 'created',
  };
  expect(commitDto(receipt).snapshot_id).toBe('snapshot');
  expect(() => commitDto({ ...receipt, snapshot_id: 123 })).toThrow();
  const legacy = { import_id: 'import', batch_id: 'batch', result: 'created' };
  expect(commitDto(legacy).snapshot_id).toBeUndefined();
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
