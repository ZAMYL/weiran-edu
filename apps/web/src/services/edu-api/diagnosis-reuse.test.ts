import { expect, it, vi } from 'vitest';
import { createEduClient } from './client';
import type { ApiBatch } from './types';
import type { DiscoverySpec } from './discovery-types';

const batch: ApiBatch = { batch_id: 'b', snapshot_id: 's', student_count: 17, title: 'Synthetic' };
const spec: DiscoverySpec = {
  snapshot_id: 's',
  scope_conditions: [],
  profile: 'edu.discovery.profile.v1',
};
const failed = () => Response.json({ error: { code: 'SYNTHETIC_FAILURE' } }, { status: 503 });

it('keeps reuse opt-in and rotates the diagnosis session on every credential connection', async () => {
  const transport = vi.fn<typeof fetch>().mockImplementation(async () => failed());
  const client = createEduClient(vi.fn(), transport);
  const read = async () => {
    await client.diagnosisDiscoveries(batch, spec).catch(() => {});
    return new Headers(transport.mock.lastCall![1]?.headers).get('x-edu-diagnosis-session');
  };
  client.authenticate('synthetic-token-a');
  const first = await read();
  expect(first).toMatch(/^[0-9a-f-]{36}$/);
  expect(await read()).toBe(first);
  client.authenticate('synthetic-token-a');
  expect(await read()).not.toBe(first);
  const second = await read();
  client.clear();
  client.useTestSession();
  expect(await read()).not.toBe(second);
  const other = createEduClient(vi.fn(), transport);
  other.authenticate('synthetic-token-a');
  await other.diagnosisDiscoveries(batch, spec).catch(() => {});
  expect(new Headers(transport.mock.lastCall![1]?.headers).get('x-edu-diagnosis-session')).not.toBe(
    first,
  );
});

it('never enables calculation reuse for ordinary finding detail or statistics/evidence access', async () => {
  const transport = vi.fn<typeof fetch>().mockImplementation(async () => failed());
  const client = createEduClient(vi.fn(), transport);
  client.authenticate('synthetic-token');
  await client.discoveries(batch, spec).catch(() => {});
  await client.discoveryDetail(batch, 'a'.repeat(24), spec).catch(() => {});
  await client.statistics(batch).catch(() => {});
  expect(transport).toHaveBeenCalledTimes(3);
  for (const [, init] of transport.mock.calls)
    expect(new Headers(init?.headers).has('x-edu-diagnosis-session')).toBe(false);
});
