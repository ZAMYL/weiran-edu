import {
  checked,
  count,
  flag,
  list,
  literal,
  nullable,
  numeric,
  object,
  optional,
  text,
  type Decoded,
} from './schema';
import { conditionDto, dimensionCode } from './types';
import { discoveryExactDto, exactRationalDto } from './exact';

const origin = {
  execution_origin: literal('http_mock', 'qwen_live'),
  real_qwen: literal('not_run', 'completed', 'failed'),
  production_enabled: flag,
};
const provenanceDto = object({
  origin: literal('qwen_live', 'http_mock', 'not_run'),
  state: literal('not_run', 'completed', 'failed'),
  request_state: literal('not_run', 'attempted', 'responses_received', 'failed'),
  model_requests: checked(count, (value) => value <= 6),
  tool_attempts: checked(count, (value) => value <= 5),
  source_replay_verified: flag,
  error_code: nullable(text),
});
export type AiTaskProvenance = Decoded<typeof provenanceDto>;
export const aiCapabilityDto = object({
  available: flag,
  mode: literal('qwen_live', 'http_mock', 'unavailable'),
  reason: literal('available', 'AI_LIVE_NOT_ENABLED'),
  message: text,
});
export type AiCapability = Decoded<typeof aiCapabilityDto>;
const sourceManifestDto = checked(text, (value) => /^[a-f0-9]{64}$/.test(value));
const explorationDto = object({ snapshot_id: text, conditions: list(conditionDto) });
const sourceReferenceDto = object({
  metric_key: text,
  dimension_code: text,
  participant_count: count,
  witness_kind: literal('score', 'row'),
});
const factDto = object({
  fact_id: text,
  batch_id: text,
  snapshot_id: text,
  method: text,
  rule_version: text,
  qualified: flag,
  source_verified: flag,
  conditions: list(conditionDto),
  metrics: list(
    object({
      key: text,
      exact: optional(nullable(exactRationalDto)),
      number: optional(nullable(numeric)),
      display: text,
    }),
  ),
  source_refs: list(sourceReferenceDto),
  limitation_codes: list(text),
  exploration_query: nullable(explorationDto),
});
export type AiFact = Decoded<typeof factDto>;
const profileConditionDto = object({
  dimension_code: dimensionCode,
  operator: literal('<', '<=', '==', '>=', '>'),
  value: text,
});
const profileStatisticDto = object({
  valid_count: count,
  missing_count: count,
  invalid_count: count,
  mean: discoveryExactDto,
  median: discoveryExactDto,
  distribution: list(object({ score: discoveryExactDto, count, student_denominator: count })),
  distribution_state: literal('available', 'privacy_limited'),
});
const profileDimensionDto = object({
  dimension_code: dimensionCode,
  label: text,
  condition_defined: flag,
  target: profileStatisticDto,
  remainder: profileStatisticDto,
  difference: discoveryExactDto,
});
const profileDto = object({
  profile_id: text,
  batch_id: text,
  snapshot_id: text,
  title: text,
  common_pattern: text,
  summary: text,
  why_show: text,
  suggested_next_action: text,
  conditions: list(profileConditionDto),
  dimensions: list(profileDimensionDto),
  exploration_query: object({ snapshot_id: text, conditions: list(profileConditionDto) }),
  source_replay_verified: literal(true),
  five_dimension_summary_origin: literal('deterministic_discovery_replay'),
  agent_fact_scope: literal('primary_non_condition_outcome_only'),
  verified_fact_ids: list(text),
  dimension_evidence_http: literal(
    'synthetic_task_profile_scores_v1',
    'live_task_profile_scores_v1',
  ),
  minimum_cell: count,
  public_display_allowed: literal(false),
  production_enabled: literal(false),
});
export type AiProfile = Decoded<typeof profileDto>;
export type AiProfileDimension = Decoded<typeof profileDimensionDto>;
const profileProjectionDto = object({
  assessment_snapshot_id: text,
  source_manifest_sha256: sourceManifestDto,
  profile_rule_version: text,
  explanation_rule_version: text,
  minimum_cell: count,
  profiles: list(profileDto),
  stop_reason: text,
  investigation_status: text,
  uninvestigated_candidates: count,
  dimension_evidence_http: literal(
    'synthetic_task_profile_scores_v1',
    'live_task_profile_scores_v1',
  ),
});
const resultDto = object({
  ...origin,
  run_id: text,
  batch_id: text,
  snapshot_id: text,
  status: text,
  stop_reason: text,
  batch_record_count: count,
  missing_evidence: list(text),
  facts: list(factDto),
  selected_fact_ids: list(text),
  retained_fact_ids: list(text),
  uncovered_direction_count: count,
  profile_projection: optional(profileProjectionDto),
});
export const aiTaskDto = checked(
  object({
    ...origin,
    task_id: text,
    batch_id: text,
    snapshot_id: text,
    campaign_id: text,
    state: literal('queued', 'running', 'completed', 'failed', 'cancelled', 'revoked', 'timed_out'),
    error_code: nullable(text),
    stop_reason: nullable(text),
    tool_attempts: count,
    model_requests: count,
    max_tool_attempts: count,
    max_model_requests: count,
    result: nullable(resultDto),
    provenance: optional(nullable(provenanceDto)),
    review_result_path: nullable(text),
    expires_at: numeric,
  }),
  (task) => {
    const provenance = task.provenance;
    if (task.execution_origin === 'http_mock') {
      if (task.real_qwen !== 'not_run' || task.production_enabled) return false;
      if (provenance && provenance.origin !== 'http_mock') return false;
    }
    if (task.execution_origin === 'qwen_live') {
      if (provenance && provenance.origin !== 'qwen_live') return false;
      if (
        task.real_qwen === 'completed' &&
        (!provenance || provenance.state !== 'completed' || !provenance.source_replay_verified)
      )
        return false;
    }
    if (task.real_qwen === 'completed' && !task.result) return false;
    if (provenance) {
      if (
        provenance.model_requests > task.max_model_requests ||
        provenance.tool_attempts > task.max_tool_attempts ||
        (provenance.origin !== task.execution_origin && provenance.origin !== 'not_run')
      )
        return false;
    }
    const result = task.result;
    if (!result) return true;
    if (
      result.execution_origin !== task.execution_origin ||
      result.real_qwen !== task.real_qwen ||
      result.production_enabled !== task.production_enabled
    )
      return false;
    const ids = new Set(result.facts.map((fact) => fact.fact_id));
    const projection = result.profile_projection;
    return (
      result.batch_id === task.batch_id &&
      result.snapshot_id === task.snapshot_id &&
      ids.size === result.facts.length &&
      result.selected_fact_ids.every((id) =>
        result.facts.some((fact) => fact.fact_id === id && fact.source_verified && fact.qualified),
      ) &&
      result.facts.every(
        (fact) =>
          fact.batch_id === task.batch_id &&
          fact.snapshot_id === task.snapshot_id &&
          (!fact.exploration_query || fact.exploration_query.snapshot_id === task.snapshot_id),
      ) &&
      (!projection ||
        (projection.assessment_snapshot_id === task.snapshot_id &&
          projection.profiles.every(
            (profile) =>
              profile.batch_id === task.batch_id &&
              profile.snapshot_id === task.snapshot_id &&
              profile.exploration_query.snapshot_id === task.snapshot_id &&
              profile.dimensions.length === 5 &&
              new Set(profile.dimensions.map((dimension) => dimension.dimension_code)).size === 5 &&
              profile.verified_fact_ids.every((id) => ids.has(id)),
          )))
    );
  },
);
export type AiTask = Decoded<typeof aiTaskDto>;
export const aiSourcesDto = object({
  batch_id: text,
  snapshot_id: text,
  run_id: text,
  fact_id: text,
  metric_key: text,
  total_count: count,
  offset: count,
  items: list(object({ student_id: text, evidence_id: text, dimension_code: text })),
});
export const aiProfileScoresDto = checked(
  object({
    task_id: text,
    batch_id: text,
    snapshot_id: text,
    profile_id: text,
    dimension_code: dimensionCode,
    side: literal('target', 'remainder'),
    detail_status: literal('available', 'privacy_limited'),
    source_origin: literal('deterministic_profile_replay'),
    source_manifest_sha256: sourceManifestDto,
    verified_profile_fact_ids: list(text),
    dimension_fact_ids: list(text),
    mean: exactRationalDto,
    valid_count: count,
    total_count: nullable(count),
    offset: count,
    limit: count,
    has_more: flag,
    items: list(
      object({
        student_id: text,
        evidence_id: text,
        dimension_code: dimensionCode,
        score: exactRationalDto,
        evidence_path: text,
      }),
    ),
    limitation_code: nullable(literal('PROFILE_SCORE_DETAIL_PRIVACY_LIMITED')),
  }),
  (value) => {
    const evidence = new Set(value.items.map((item) => item.evidence_id));
    const common =
      value.limit >= 1 &&
      value.limit <= 100 &&
      value.items.length <= value.limit &&
      evidence.size === value.items.length &&
      value.items.every((item) => item.dimension_code === value.dimension_code);
    return value.detail_status === 'available'
      ? common && value.total_count !== null && value.limitation_code === null
      : common &&
          value.total_count === null &&
          value.items.length === 0 &&
          !value.has_more &&
          value.limitation_code === 'PROFILE_SCORE_DETAIL_PRIVACY_LIMITED';
  },
);
export type AiProfileScores = Decoded<typeof aiProfileScoresDto>;
export const aiReviewDto = object({
  task_id: text,
  batch_id: text,
  snapshot_id: text,
  result_id: text,
  version: count,
  candidate_ids: list(text),
  status: literal('pending_teacher_review'),
  agent_rule_version: text,
  formal_rule_version: text,
});
export const aiRunning = (task: AiTask | null) =>
  !!task && (task.state === 'queued' || task.state === 'running');
export const taskStateLabels: Record<AiTask['state'], string> = {
  queued: '已排队',
  running: '正在分析',
  completed: '分析已结束',
  failed: '分析失败',
  cancelled: '已停止',
  revoked: '访问权限已撤销',
  timed_out: '分析超时',
};
export const stopLabels: Record<string, string> = {
  bounded_scope_stop: '核查未覆盖所有问题',
  goal_satisfied: '本轮问题已完成核查',
  needs_external_evidence: '已保留可核验结果',
  no_supported_findings: '本轮未发现符合条件的结果',
  budget_limited: '本轮分析额度已用尽',
  stopped_with_uncovered_directions: '本轮已结束，仍有方向未核查',
};
