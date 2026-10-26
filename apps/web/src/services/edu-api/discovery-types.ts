import {
  checked,
  ContractError,
  count,
  type Decoded,
  flag,
  list,
  literal,
  nullable,
  numeric,
  object,
  text,
} from './schema';
import {
  conditionDto,
  dimensionCode,
  pageDto,
  scoreDto,
  statisticsDto,
  type ApiCondition,
} from './types';

export interface DiscoverySpec {
  snapshot_id: string;
  scope_conditions: ApiCondition[];
  profile: 'edu.discovery.profile.v1';
}
const exact = checked(
  object({ numerator: numeric, denominator: count, display: text }),
  (v) => Number.isSafeInteger(v.numerator) && v.denominator > 0,
);
export type DiscoveryExact = Decoded<typeof exact>;
const condition = object({
  dimension_code: dimensionCode,
  operator: literal('lt', 'lte', 'eq', 'gte', 'gt'),
  threshold: exact,
});
const scope = checked(
  object({
    batch_id: text,
    snapshot_id: text,
    input_key: text,
    profile: literal('edu.discovery.profile.v1'),
    scope_conditions: list(conditionDto),
    batch_record_count: count,
    scope_record_count: count,
    eligible_count: count,
    unevaluable_count: count,
    statistics: statisticsDto,
    public_display_allowed: literal(false),
  }),
  (v) =>
    v.statistics.batch_id === v.batch_id &&
    v.statistics.snapshot_id === v.snapshot_id &&
    v.statistics.scope.record_count === v.scope_record_count &&
    v.scope_record_count <= v.batch_record_count,
);
const summaryShape = {
  discovery_id: text,
  batch_id: text,
  snapshot_id: text,
  input_key: text,
  rule_version: text,
  type: literal(
    'dimension_relation',
    'group_difference',
    'rare_combination',
    'boundary_sensitivity',
  ),
  title: text,
  summary: text,
  status: literal('displayable', 'interpret_cautiously', 'insufficient_evidence'),
  public_display_allowed: literal(false),
};
const summary = object(summaryShape);
export type DiscoverySummary = Decoded<typeof summary>;
const same = (
  v: { batch_id: string; snapshot_id: string; input_key: string },
  s: Decoded<typeof scope>,
) => v.batch_id === s.batch_id && v.snapshot_id === s.snapshot_id && v.input_key === s.input_key;
export const discoveriesDto = checked(
  object({
    scope,
    rule_version: text,
    qualified_count: count,
    retained_count: count,
    truncated: flag,
    items: list(summary),
    page: pageDto,
  }),
  (v) =>
    v.retained_count <= v.qualified_count &&
    v.items.length === v.page.returned &&
    v.items.every((i) => same(i, v.scope) && i.rule_version === v.rule_version),
);
const stats = object({
  valid_count: count,
  missing_count: count,
  invalid_count: count,
  mean: nullable(exact),
  median: nullable(exact),
  distribution: list(
    object({
      score: exact,
      count,
      student_denominator: count,
    }),
  ),
});
const relationShape = {
  type: literal('dimension_relation'),
  metrics: object({
    left: dimensionCode,
    right: dimensionCode,
    rho: nullable(object({ value: numeric, method: literal('stdlib_float') })),
    paired_count: count,
    excluded_count: count,
    student_denominator: count,
  }),
  chart: list(object({ x: exact, y: exact, count })),
};
const groupShape = {
  type: literal('group_difference'),
  metrics: object({
    outcome_dimension: dimensionCode,
    target_count: count,
    remainder_count: count,
    unevaluable_count: count,
    student_denominator: count,
    eligible_denominator: count,
    share_total: nullable(exact),
    share_eligible: nullable(exact),
  }),
  chart: list(
    object({
      dimension_code: dimensionCode,
      condition_defined: flag,
      target: stats,
      remainder: stats,
      difference: nullable(exact),
    }),
  ),
};
const rareShape = {
  type: literal('rare_combination'),
  metrics: object({
    left: dimensionCode,
    right: dimensionCode,
    pair_count: count,
    excluded_count: count,
    match_count: count,
    pattern: literal('individual_review', 'small_group_review', 'group_pattern'),
    share_pairs: exact,
    q1: exact,
    q3: exact,
    lower_fence: exact,
    upper_fence: exact,
    side: literal('lower', 'upper'),
    student_denominator: count,
  }),
  chart: list(object({ difference: exact, count })),
};
const boundaryShape = {
  type: literal('boundary_sensitivity'),
  metrics: object({
    strict_count: count,
    inclusive_count: count,
    added_count: count,
    removed_count: literal(0),
    student_denominator: count,
    eligible_denominator: count,
    unevaluable_count: count,
    share_strict_total: nullable(exact),
    share_inclusive_total: nullable(exact),
    share_strict_eligible: nullable(exact),
    share_inclusive_eligible: nullable(exact),
  }),
  chart: list(object({ operator: literal('lt', 'lte'), count })),
};
const kinds = (value: unknown) => {
  if (!value || typeof value !== 'object' || !('type' in value)) throw new ContractError();
  switch (value.type) {
    case 'dimension_relation':
      return object(relationShape)(value);
    case 'group_difference':
      return object(groupShape)(value);
    case 'rare_combination':
      return object(rareShape)(value);
    case 'boundary_sensitivity':
      return object(boundaryShape)(value);
    default:
      throw new ContractError();
  }
};
const chartBase = object({
  scope,
  discovery_id: text,
  input_key: text,
  rule_version: text,
  conditions: list(condition),
  public_display_allowed: literal(false),
});
export const discoveryChartDto = (value: unknown) => {
  const base = chartBase(value);
  if (base.input_key !== base.scope.input_key) throw new ContractError();
  return { ...base, ...kinds(value) };
};
export type DiscoveryChart = ReturnType<typeof discoveryChartDto>;
const detailBase = object({
  ...summaryShape,
  scope,
  rule_id: text,
  conditions: list(condition),
  limitations: list(text),
  controlled_queries: list(object({ discovery_id: text, metric_key: text })),
  parameters: object({
    minimum_samples: count,
    support_target: count,
    search_budget: count,
    maximum_findings: count,
    maximum_students: count,
    correlation_threshold: exact,
    mean_difference: exact,
    rare_share: exact,
    minimum_contrast: exact,
  }),
  ranking: object({
    score: exact,
    effect: exact,
    support_count: count,
    support: exact,
    completeness: exact,
    robustness: object({
      method: text,
      planned: count,
      evaluated: count,
      passed: count,
      value: nullable(exact),
    }),
    is_statistical_confidence: literal(false),
  }),
});
export const discoveryDetailDto = (value: unknown) => {
  const base = detailBase(value);
  if (
    !same(base, base.scope) ||
    base.controlled_queries.some((q) => q.discovery_id !== base.discovery_id)
  )
    throw new ContractError();
  return { ...base, ...kinds(value) };
};
export type DiscoveryDetail = ReturnType<typeof discoveryDetailDto>;
export const discoveryRecordsDto = checked(
  object({
    scope,
    metric_key: text,
    total_count: count,
    items: list(object({ student_id: text, display_label: text, scores: list(scoreDto) })),
    page: pageDto,
    discovery_id: text,
    rule_version: text,
    conditions: list(condition),
    source_dimensions: list(dimensionCode),
  }),
  (v) =>
    v.page.returned === v.items.length &&
    v.items.length <= v.total_count &&
    v.items.every(
      (i) =>
        i.scores.length === v.source_dimensions.length &&
        new Set(i.scores.map((s) => s.dimension_code)).size === i.scores.length &&
        i.scores.every((s) => v.source_dimensions.includes(s.dimension_code)),
    ),
);
