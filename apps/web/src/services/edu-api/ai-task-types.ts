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
import { conditionDto } from './types';
import { exactRationalDto } from './exact';

const origin = {
  execution_origin: literal('http_mock'),
  real_qwen: literal('not_run'),
  production_enabled: literal(false),
};
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
    review_result_path: nullable(text),
    expires_at: numeric,
  }),
  (task) => {
    const result = task.result;
    if (!result) return true;
    const ids = new Set(result.facts.map((fact) => fact.fact_id));
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
      )
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
  goal_satisfied: '本轮问题已完成核查',
  needs_external_evidence: '已保留有效发现，进一步解释还需要量规或答卷等资料',
  no_supported_findings: '本轮未发现符合条件的结果',
  budget_limited: '本轮分析额度已用尽',
  stopped_with_uncovered_directions: '本轮已结束，仍有方向未核查',
};
