import { expect, it } from 'vitest';
import { discoveryExactDto, exactInteger, exactRationalDto, exactToNumber } from './exact';
import { count } from './schema';
import { pageDto, statValueDto } from './types';

const large = '24840307107174983';
it('preserves safe legacy integers and canonical oversized strings without BigInt in DTOs', () => {
  for (const n of [0, -1, Number.MAX_SAFE_INTEGER, -Number.MAX_SAFE_INTEGER])
    expect(exactInteger(n)).toBe(n);
  for (const n of [large, '-' + large, '9'.repeat(1024), '-' + '9'.repeat(1024)])
    expect(exactInteger(n)).toBe(n);
  const v = discoveryExactDto({
    numerator: large,
    denominator: '25000000000000000',
    display: '0.9936',
  });
  expect(JSON.parse(JSON.stringify(v))).toEqual(v);
  expect(exactToNumber(v)).toBe(0.9936122842869993);
  expect(
    statValueDto({ value: 1, exact: { numerator: large, denominator: large }, display: '1.00' })
      .exact?.numerator,
  ).toBe(large);
});

it.each([
  Number.MAX_SAFE_INTEGER + 1,
  -(Number.MAX_SAFE_INTEGER + 1),
  1.2,
  NaN,
  Infinity,
  true,
  null,
  {},
  BigInt(1),
  '0',
  '-0',
  '1',
  '-1',
  String(Number.MAX_SAFE_INTEGER),
  '0' + large,
  '+' + large,
  large + ' ',
  large + '\n',
  '  ' + large,
  '1e30',
  '1.1',
  '９'.repeat(20),
  '9'.repeat(1025),
  '-' + '9'.repeat(1025),
])('rejects invalid/unsafe or noncanonical integer %s', (n) => {
  expect(() => exactInteger(n)).toThrow('INVALID_API_RESPONSE');
});

it('retains positive denominators, display format and ordinary count/page/version validation', () => {
  for (const denominator of [0, -1, '-' + large, '1', '0', true])
    expect(() => exactRationalDto({ numerator: 1, denominator })).toThrow();
  for (const display of [
    '1.00',
    '01.0000',
    '+1.0000',
    '1e0',
    'NaN',
    'Infinity',
    '1.0000\n',
    '9'.repeat(316) + '.0000',
  ])
    expect(() => discoveryExactDto({ numerator: 1, denominator: 1, display })).toThrow();
  expect(
    discoveryExactDto({ numerator: -1, denominator: 1000000, display: '-0.0000' }).display,
  ).toBe('-0.0000');
  expect(count(0)).toBe(0);
  for (const n of [large, Number.MAX_SAFE_INTEGER + 1, -1, 1.5]) {
    expect(() => count(n)).toThrow();
    expect(() => pageDto({ offset: n, limit: 10, returned: 0, has_more: false })).toThrow();
  }
});

it('converts huge ratios with BigInt division, including signs, ties, subnormals and overflow', () => {
  const digits = '9'.repeat(1024);
  expect(exactToNumber(exactRationalDto({ numerator: digits, denominator: digits }))).toBe(1);
  expect(exactToNumber(exactRationalDto({ numerator: '-' + digits, denominator: digits }))).toBe(
    -1,
  );
  expect(exactToNumber({ numerator: 1, denominator: 3 })).toBe(1 / 3);
  const power = BigInt(2) ** BigInt(1074);
  expect(exactToNumber({ numerator: 1, denominator: power.toString() })).toBe(Number.MIN_VALUE);
  expect(exactToNumber({ numerator: -1, denominator: (power * BigInt(2)).toString() })).toBe(-0);
  expect(exactToNumber({ numerator: 3, denominator: (power * BigInt(2)).toString() })).toBe(
    2 * Number.MIN_VALUE,
  );
  expect(exactToNumber({ numerator: '9007199254740993', denominator: '9007199254740992' })).toBe(1);
  expect(exactToNumber({ numerator: '9007199254740995', denominator: '9007199254740992' })).toBe(
    1 + 2 ** -51,
  );
  expect(() => exactToNumber({ numerator: digits, denominator: 1 })).toThrow(
    'INVALID_API_RESPONSE',
  );
});
