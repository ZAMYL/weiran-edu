import type { DiscoveryDetail, DiscoverySpec } from './discovery-types';
import { parseThreshold } from './view-model';
import { conditionDto } from './types';
export interface DiscoveryOrigin {
  batch_id: string;
  discovery_id: string;
  metric_key: string;
  spec: DiscoverySpec;
}
// A navigation pointer is never authorization; the server replays all references.
export function discoveryUrl(origin: DiscoveryOrigin): string {
  return `/explorer?discovery=${encodeURIComponent(JSON.stringify(origin))}`;
}
export function readDiscoveryOrigin(search: string): DiscoveryOrigin | null {
  const raw = new URLSearchParams(search).get('discovery');
  if (!raw || raw.length > 4000) return null;
  try {
    const o = JSON.parse(raw);
    if (
      !o ||
      typeof o !== 'object' ||
      typeof o.batch_id !== 'string' ||
      typeof o.discovery_id !== 'string' ||
      !/^[a-zA-Z0-9_-]{1,100}$/.test(o.batch_id) ||
      !/^[a-f0-9]{24}$/.test(o.discovery_id) ||
      typeof o.metric_key !== 'string' ||
      !/^[a-zA-Z_.]{1,100}$/.test(o.metric_key) ||
      typeof o.spec?.snapshot_id !== 'string' ||
      o.spec.snapshot_id.length > 100 ||
      o.spec.profile !== 'edu.discovery.profile.v1' ||
      !Array.isArray(o.spec.scope_conditions) ||
      o.spec.scope_conditions.length > 2
    )
      return null;
    const conditions = o.spec.scope_conditions.map((c: unknown) => conditionDto(c));
    if (
      conditions.some(
        (c: ReturnType<typeof conditionDto>) => parseThreshold(String(c.value)) === null,
      ) ||
      new Set(conditions.map((c: ReturnType<typeof conditionDto>) => c.dimension_code)).size !==
        conditions.length
    )
      return null;
    return {
      batch_id: o.batch_id,
      discovery_id: o.discovery_id,
      metric_key: o.metric_key,
      spec: {
        snapshot_id: o.spec.snapshot_id,
        profile: 'edu.discovery.profile.v1',
        scope_conditions: conditions,
      },
    };
  } catch {
    return null;
  }
}

export function originForDiscovery(
  detail: DiscoveryDetail,
  spec: DiscoverySpec,
): DiscoveryOrigin | null {
  const metric_key = {
    dimension_relation: 'paired_scores',
    group_difference: 'target_members',
    rare_combination: 'rare_members',
    boundary_sensitivity: 'inclusive_members',
  }[detail.type];
  if (
    !detail.controlled_queries.some(
      (query) => query.metric_key === metric_key && query.discovery_id === detail.discovery_id,
    )
  )
    return null;
  return {
    batch_id: detail.batch_id,
    discovery_id: detail.discovery_id,
    metric_key,
    spec: {
      ...spec,
      scope_conditions: spec.scope_conditions.map((condition) => ({ ...condition })),
    },
  };
}
