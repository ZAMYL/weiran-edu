// Private wire decoders. Fail closed on malformed responses; never coerce scores or counts.
export type Decoder<T> = (value: unknown) => T;
export type Decoded<D> = D extends Decoder<infer T> ? T : never;
export class ContractError extends Error {
  constructor() {
    super('INVALID_API_RESPONSE');
  }
}
export const text: Decoder<string> = (value) => {
  if (typeof value !== 'string') throw new ContractError();
  return value;
};
export const numeric: Decoder<number> = (value) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new ContractError();
  return value;
};
export const count: Decoder<number> = (value) => {
  const n = numeric(value);
  if (!Number.isSafeInteger(n) || n < 0) throw new ContractError();
  return n;
};
export const flag: Decoder<boolean> = (value) => {
  if (typeof value !== 'boolean') throw new ContractError();
  return value;
};
export const empty: Decoder<null> = (value) => {
  if (value !== null) throw new ContractError();
  return null;
};
export const literal =
  <const T extends string | number | boolean>(...values: T[]): Decoder<T> =>
  (value) => {
    if (!values.some((v) => v === value)) throw new ContractError();
    return value as T;
  };
export const nullable =
  <T>(decode: Decoder<T>): Decoder<T | null> =>
  (value) =>
    value === null ? null : decode(value);
export const optional =
  <T>(decode: Decoder<T>): Decoder<T | undefined> =>
  (value) =>
    value === undefined ? undefined : decode(value);
export const checked =
  <T>(decode: Decoder<T>, valid: (value: T) => boolean): Decoder<T> =>
  (value) => {
    const result = decode(value);
    if (!valid(result)) throw new ContractError();
    return result;
  };
export const list =
  <T>(decode: Decoder<T>): Decoder<T[]> =>
  (value) => {
    if (!Array.isArray(value)) throw new ContractError();
    return value.map(decode);
  };
export function object<S extends Record<string, Decoder<unknown>>>(
  shape: S,
): Decoder<{ [K in keyof S]: Decoded<S[K]> }> {
  return (value) => {
    if (value === null || typeof value !== 'object' || Array.isArray(value))
      throw new ContractError();
    const source = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.entries(shape).map(([key, decode]) => [key, decode(source[key])]),
    ) as { [K in keyof S]: Decoded<S[K]> };
  };
}
