import { checked, ContractError, object, text, type Decoder } from './schema';

// EXACT_JSON_WIRE_V1: keep DTOs JSON-serializable; BigInt exists only during validation/math.
export type ExactInteger = number | string;
const SAFE = BigInt(Number.MAX_SAFE_INTEGER);
export const exactInteger: Decoder<ExactInteger> = (value) => {
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw new ContractError();
    return value;
  }
  if (
    typeof value !== 'string' ||
    value.length > 1025 ||
    !/^-?[1-9][0-9]*(?![\s\S])/.test(value) ||
    value.replace(/^-/, '').length > 1024
  )
    throw new ContractError();
  const integer = BigInt(value);
  if (integer >= -SAFE && integer <= SAFE) throw new ContractError();
  return value;
};
const denominator = checked(exactInteger, (v) => BigInt(v) > BigInt(0));
export const exactRationalDto = object({ numerator: exactInteger, denominator });
export type ExactRational = ReturnType<typeof exactRationalDto>;

// Round the rational to binary64 only after bounded BigInt division, including ties-to-even.
// Converting either component first loses bits and can produce Infinity / Infinity.
export function exactToNumber(value: ExactRational): number {
  let n = BigInt(value.numerator);
  const d = BigInt(value.denominator);
  const negative = n < BigInt(0);
  if (negative) n = -n;
  if (n === BigInt(0)) return 0;
  let exponent = n.toString(2).length - d.toString(2).length;
  if (exponent >= 0 ? n < d << BigInt(exponent) : n << BigInt(-exponent) < d) exponent--;
  if (exponent > 1023) throw new ContractError();
  if (exponent < -1075) return negative ? -0 : 0;
  const shift = exponent < -1022 ? 1074 : 52 - exponent;
  const dividend = shift >= 0 ? n << BigInt(shift) : n;
  const divisor = shift >= 0 ? d : d << BigInt(-shift);
  let quotient = dividend / divisor;
  const twiceRemainder = (dividend % divisor) * BigInt(2);
  if (
    twiceRemainder > divisor ||
    (twiceRemainder === divisor && quotient % BigInt(2) !== BigInt(0))
  )
    quotient++;
  const result = Number(quotient) * 2 ** -shift;
  if (!Number.isFinite(result)) throw new ContractError();
  return negative ? -result : result;
}

export const discoveryExactDto = checked(
  object({ numerator: exactInteger, denominator, display: text }),
  (v) =>
    v.display.length <= 320 &&
    /^-?(?:0|[1-9][0-9]*)\.[0-9]{4}(?![\s\S])/.test(v.display) &&
    Number.isFinite(Number(v.display)) &&
    Number.isFinite(exactToNumber(v)),
);
