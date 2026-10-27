import { expect, it } from 'vitest';
import { discoveryUrl, readDiscoveryOrigin, type DiscoveryOrigin } from './discovery-navigation';
const origin: DiscoveryOrigin = {
  batch_id: 'batch',
  discovery_id: 'a'.repeat(24),
  metric_key: 'target_members',
  spec: {
    snapshot_id: 'snapshot',
    profile: 'edu.discovery.profile.v1',
    scope_conditions: [{ dimension_code: 'algorithm_data_structure', operator: 'lt', value: 2.75 }],
  },
};
it('round-trips exact outer conditions and original membership without student data or credentials', () => {
  const url = discoveryUrl(origin);
  expect(readDiscoveryOrigin(new URL(url, 'http://localhost').search)).toEqual(origin);
  expect(url).not.toMatch(/token|student_id|evidence_id/);
});
it.each([
  'malformed',
  'batch',
  'discovery',
  'metric',
  'threshold',
  'precision',
  'operator',
  'duplicate',
  'profile',
])('rejects invalid navigation %s', (kind) => {
  const o = structuredClone(origin);
  if (kind === 'batch') o.batch_id = '../private';
  if (kind === 'discovery') o.discovery_id = 'unknown';
  if (kind === 'metric') o.metric_key = '../private';
  if (kind === 'threshold') o.spec.scope_conditions[0]!.value = 6;
  if (kind === 'precision') o.spec.scope_conditions[0]!.value = 2.755;
  if (kind === 'operator') Object.assign(o.spec.scope_conditions[0]!, { operator: 'execute' });
  if (kind === 'duplicate') o.spec.scope_conditions.push({ ...o.spec.scope_conditions[0]! });
  if (kind === 'profile') Object.assign(o.spec, { profile: 'unknown' });
  expect(
    readDiscoveryOrigin(
      kind === 'malformed' ? '?discovery=%7B' : new URL(discoveryUrl(o), 'http://localhost').search,
    ),
  ).toBeNull();
});
