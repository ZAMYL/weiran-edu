import { expect, it, vi } from 'vitest';
import { createEduClient } from './client';
import { discoveryChartDto, type DiscoverySpec } from './discovery-types';
import { DIMENSIONS, type ApiBatch } from './types';

const exact = { numerator: 3, denominator: 1, display: '3.0000' };
const stat = { value: 3, exact: { numerator: 3, denominator: 1 }, display: '3.00' };
const scope = {
  batch_id: 'b',
  snapshot_id: 's',
  input_key: 'k',
  profile: 'edu.discovery.profile.v1',
  scope_conditions: [],
  batch_record_count: 53,
  scope_record_count: 53,
  eligible_count: 53,
  unevaluable_count: 0,
  public_display_allowed: false,
  statistics: {
    batch_id: 'b',
    snapshot_id: 's',
    rule_version: 'v',
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
      distribution: [{ score: 3, count: 53, denominator: 53, ratio: { ...stat, value: 1 } }],
    })),
  },
};
const chart = {
  scope,
  discovery_id: 'id',
  input_key: 'k',
  rule_version: 'v',
  type: 'dimension_relation',
  conditions: [],
  public_display_allowed: false,
  metrics: {
    left: 'algorithm_data_structure',
    right: 'solution_strategy',
    rho: { value: 1, method: 'stdlib_float' },
    paired_count: 53,
    excluded_count: 0,
    student_denominator: 53,
  },
  chart: [{ x: exact, y: exact, count: 53 }],
};
const spec: DiscoverySpec = {
  snapshot_id: 's',
  profile: 'edu.discovery.profile.v1',
  scope_conditions: [],
};
const batch = { batch_id: 'b', snapshot_id: 's' } as ApiBatch;
const reply = (data: unknown, status = 200) => Response.json(data, { status });
it('decodes chart by kind, keeps complete counts and rejects mismatched shapes or hidden state', () => {
  expect(discoveryChartDto(chart).metrics.student_denominator).toBe(53);
  for (const broken of [
    { ...chart, type: 'group_difference' },
    { ...chart, input_key: 'other' },
    { ...chart, public_display_allowed: true },
    { ...chart, chart: [{ x: exact, y: exact, count: 1.2 }] },
    { ...chart, chart: [{ x: { ...exact, denominator: 0 }, y: exact, count: 1 }] },
  ])
    expect(() => discoveryChartDto(broken)).toThrow();
});
it('decodes lossless chart/threshold/statistics components without changing counts or order', () => {
  const value = structuredClone(chart);
  const huge = { numerator: '9'.repeat(1024), denominator: '9'.repeat(1024), display: '1.0000' };
  Object.assign(value.chart[0]!.x, huge);
  Object.assign(value.scope.statistics.dimensions[0]!.mean.exact, huge);
  const decoded = discoveryChartDto({
    ...value,
    conditions: [{ dimension_code: 'code_implementation', operator: 'gt', threshold: huge }],
  });
  expect(decoded.chart).toEqual(value.chart);
  expect(decoded.conditions[0]?.threshold).toEqual(huge);
  expect(decoded.scope.statistics.dimensions[0]?.mean.exact?.numerator).toBe(huge.numerator);
  expect(decoded.metrics.student_denominator).toBe(53);
  expect(() =>
    discoveryChartDto({ ...value, chart: [{ ...value.chart[0], count: huge.numerator }] }),
  ).toThrow();
});
it('sends original outer scope and metric through the controlled records endpoint', async () => {
  const scoped: DiscoverySpec = {
    ...spec,
    scope_conditions: [{ dimension_code: 'code_implementation', operator: 'lte', value: 3 }],
  };
  const transport = vi.fn<typeof fetch>().mockResolvedValue(
    reply({
      scope: { ...scope, scope_conditions: scoped.scope_conditions },
      metric_key: 'rare_members',
      total_count: 17,
      items: [],
      page: { offset: 20, limit: 10, returned: 0, has_more: false },
      discovery_id: 'id',
      rule_version: 'v',
      conditions: [],
      source_dimensions: ['code_implementation'],
    }),
  );
  const client = createEduClient(vi.fn(), transport);
  client.useTestSession();
  const result = await client.discoveryRecords(batch, 'id', 'rare_members', scoped, 20);
  expect(result.total_count).toBe(17);
  expect(result.items).toEqual([]);
  const [url, init] = transport.mock.calls[0]!;
  expect(url).toBe('/api/edu/batches/b/discoveries/id/records/query');
  expect(JSON.parse(String(init?.body))).toEqual({
    ...scoped,
    metric_key: 'rare_members',
    offset: 20,
    limit: 10,
  });
  expect(new Headers(init?.headers).has('authorization')).toBe(false);
});
it('rejects cross-snapshot, wrong discovery, different scope, and substituted source metrics', async () => {
  for (const response of [
    { ...chart, scope: { ...scope, snapshot_id: 'old' } },
    { ...chart, discovery_id: 'other' },
    {
      ...chart,
      scope: {
        ...scope,
        scope_conditions: [{ dimension_code: 'code_implementation', operator: 'lte', value: 3 }],
      },
    },
  ]) {
    const client = createEduClient(
      vi.fn(),
      vi.fn<typeof fetch>().mockResolvedValue(reply(response)),
    );
    client.useTestSession();
    await expect(client.discoveryChart(batch, 'id', spec)).rejects.toHaveProperty('status');
  }
  const client = createEduClient(
    vi.fn(),
    vi.fn<typeof fetch>().mockResolvedValue(
      reply({
        scope,
        metric_key: 'pair_reference',
        total_count: 17,
        items: [],
        page: { offset: 0, limit: 10, returned: 0, has_more: false },
        discovery_id: 'id',
        rule_version: 'v',
        conditions: [],
        source_dimensions: ['code_implementation'],
      }),
    ),
  );
  client.useTestSession();
  await expect(client.discoveryRecords(batch, 'id', 'rare_members', spec)).rejects.toMatchObject({
    status: 502,
    code: 'INVALID_API_RESPONSE',
  });
});
it('treats AND order and JSON property order as the same scope', async () => {
  const scoped: DiscoverySpec = {
    ...spec,
    scope_conditions: [
      { value: 3, operator: 'lte', dimension_code: 'code_implementation' },
      { operator: 'gte', value: 4, dimension_code: 'solution_strategy' },
    ],
  };
  const client = createEduClient(
    vi.fn(),
    vi.fn<typeof fetch>().mockResolvedValue(
      reply({
        ...chart,
        scope: {
          ...scope,
          scope_conditions: [
            { dimension_code: 'solution_strategy', operator: 'gte', value: 4 },
            { dimension_code: 'code_implementation', operator: 'lte', value: 3 },
          ],
        },
      }),
    ),
  );
  client.useTestSession();
  expect((await client.discoveryChart(batch, 'id', scoped)).scope.scope_record_count).toBe(53);
});
it.each([401, 404, 409, 503])(
  'preserves backend discovery error %s without falling back to demo',
  async (status) => {
    const revoked = vi.fn();
    const client = createEduClient(
      revoked,
      vi.fn<typeof fetch>().mockResolvedValue(
        reply(
          {
            error: {
              code: status === 409 ? 'SNAPSHOT_MISMATCH' : 'DISCOVERY_TIMEOUT',
              message: 'private',
            },
          },
          status,
        ),
      ),
    );
    client.useTestSession();
    await expect(client.discoveries(batch, spec)).rejects.toMatchObject({ status });
    expect(revoked).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
  },
);
