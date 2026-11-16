import { expect, it } from 'vitest';
import { aiProfileScoresDto, aiTaskDto, aiRunning, stopLabels } from './ai-task-types';
const task = {
  execution_origin: 'http_mock',
  real_qwen: 'not_run',
  production_enabled: false,
  task_id: 't',
  batch_id: 'b',
  snapshot_id: 's',
  campaign_id: 'c',
  state: 'queued',
  error_code: null,
  stop_reason: null,
  tool_attempts: 0,
  model_requests: 0,
  max_tool_attempts: 5,
  max_model_requests: 6,
  result: null,
  review_result_path: null,
  expires_at: 1,
};
it('describes a bounded stop without implying exhaustive discovery', () => {
  expect(stopLabels.bounded_scope_stop).toContain('未覆盖所有问题');
});
it('accepts actual queued and stopped states without manufacturing progress', () => {
  expect(aiRunning(aiTaskDto(task))).toBe(true);
  expect(
    aiRunning(aiTaskDto({ ...task, state: 'cancelled', error_code: 'AI_TASK_CANCELLED' })),
  ).toBe(false);
});
it('keeps live execution distinct and refuses unexecuted or mixed-source success', () => {
  const live = {
    ...task,
    origin: 'qwen_live',
    execution_origin: undefined,
    real_qwen: undefined,
    production_enabled: undefined,
  };
  expect(aiTaskDto(live).execution_origin).toBe('qwen_live');
  const result = {
    execution_origin: 'qwen_live',
    real_qwen: 'completed',
    run_id: 'r',
    batch_id: 'b',
    snapshot_id: 's',
    status: 'completed',
    stop_reason: 'no_supported_findings',
    batch_record_count: 20,
    missing_evidence: [],
    facts: [],
    selected_fact_ids: [],
    retained_fact_ids: [],
    uncovered_direction_count: 0,
  };
  const provenance = {
    origin: 'qwen_live',
    state: 'completed',
    request_state: 'responses_received',
    model_requests: 1,
    tool_attempts: 0,
    source_replay_verified: true,
    error_code: null,
  };
  const completed = { ...live, state: 'completed', model_requests: 1, provenance, result };
  expect(aiTaskDto(completed)).toMatchObject({ execution_origin: 'qwen_live', provenance });
  expect(
    aiTaskDto({ ...completed, result: { ...result, execution_origin: undefined } }).result
      ?.real_qwen,
  ).toBe('completed');
  for (const invalid of [
    { ...completed, model_requests: 0 },
    { ...completed, real_qwen: 'not_run' },
    { ...completed, origin: undefined, execution_origin: 'qwen_live' },
    { ...completed, origin: 'not_run' },
    { ...completed, execution_origin: 'http_mock' },
    { ...completed, state: 'running' },
    { ...completed, provenance: undefined },
    { ...completed, provenance: { ...provenance, source_replay_verified: false } },
    { ...completed, provenance: { ...provenance, request_state: 'attempted' } },
    { ...completed, provenance: { ...provenance, origin: 'http_mock' } },
    { ...completed, provenance: { ...provenance, model_requests: 2 } },
    { ...completed, result: { ...result, real_qwen: 'not_run' } },
    { ...completed, execution_origin: 'not_run', result: null, model_requests: 0 },
    {
      ...completed,
      result: {
        ...result,
        execution_origin: 'http_mock',
        real_qwen: 'not_run',
        production_enabled: false,
      },
    },
  ]) {
    expect(() => aiTaskDto(invalid)).toThrow('INVALID_API_RESPONSE');
  }
});
it('rejects live-provider claims and results bound to another snapshot', () => {
  expect(() => aiTaskDto({ ...task, real_qwen: 'completed' })).toThrow('INVALID_API_RESPONSE');
  expect(() => aiTaskDto({ ...task, production_enabled: true })).toThrow('INVALID_API_RESPONSE');
  const result = {
    execution_origin: 'http_mock',
    real_qwen: 'not_run',
    production_enabled: false,
    run_id: 'r',
    batch_id: 'b',
    snapshot_id: 'other',
    status: 'completed',
    stop_reason: 'no_supported_findings',
    batch_record_count: 20,
    missing_evidence: [],
    facts: [],
    selected_fact_ids: [],
    retained_fact_ids: [],
    uncovered_direction_count: 0,
  };
  expect(() => aiTaskDto({ ...task, state: 'completed', result })).toThrow('INVALID_API_RESPONSE');
});
it('decodes a verified five-dimension projection without treating every dimension as an Agent Fact', () => {
  const exact = { numerator: 3, denominator: 1, display: '3.0000' };
  const statistic = {
    valid_count: 24,
    missing_count: 0,
    invalid_count: 0,
    mean: exact,
    median: exact,
    distribution: [{ score: exact, count: 24, student_denominator: 24 }],
    distribution_state: 'available',
  };
  const fact = {
    fact_id: 'f',
    batch_id: 'b',
    snapshot_id: 's',
    method: 'compare_student_groups',
    rule_version: 'r',
    qualified: true,
    source_verified: true,
    conditions: [],
    metrics: [],
    source_refs: [],
    limitation_codes: [],
    exploration_query: null,
  };
  const dimensions = [
    'algorithm_data_structure',
    'code_implementation',
    'problem_modeling',
    'efficiency_awareness',
    'solution_strategy',
  ].map((dimension_code) => ({
    dimension_code,
    label: dimension_code,
    condition_defined: false,
    target: statistic,
    remainder: statistic,
    difference: { numerator: 0, denominator: 1, display: '0.0000' },
  }));
  const result = {
    execution_origin: 'http_mock',
    real_qwen: 'not_run',
    production_enabled: false,
    run_id: 'r',
    batch_id: 'b',
    snapshot_id: 's',
    status: 'completed',
    stop_reason: 'goal_satisfied',
    batch_record_count: 72,
    missing_evidence: [],
    facts: [fact],
    selected_fact_ids: ['f'],
    retained_fact_ids: ['f'],
    uncovered_direction_count: 0,
    profile_projection: {
      assessment_snapshot_id: 's',
      source_manifest_sha256: 'a'.repeat(64),
      profile_rule_version: 'profile-v1',
      explanation_rule_version: 'explanation-v1',
      minimum_cell: 5,
      stop_reason: 'goal_satisfied',
      investigation_status: 'complete',
      uninvestigated_candidates: 0,
      dimension_evidence_http: 'synthetic_task_profile_scores_v1',
      profiles: [
        {
          profile_id: 'profile-1',
          batch_id: 'b',
          snapshot_id: 's',
          title: '合成群体画像',
          common_pattern: '合成共同特征',
          summary: '合成摘要',
          why_show: '合成展示依据',
          suggested_next_action: '继续核验',
          conditions: [],
          dimensions,
          exploration_query: { snapshot_id: 's', conditions: [] },
          source_replay_verified: true,
          five_dimension_summary_origin: 'deterministic_discovery_replay',
          agent_fact_scope: 'primary_non_condition_outcome_only',
          verified_fact_ids: ['f'],
          dimension_evidence_http: 'synthetic_task_profile_scores_v1',
          minimum_cell: 5,
          public_display_allowed: false,
          production_enabled: false,
        },
      ],
    },
  };
  const decoded = aiTaskDto({ ...task, state: 'completed', result });
  expect(decoded.result?.profile_projection?.profiles[0]?.dimensions).toHaveLength(5);
  expect(() =>
    aiTaskDto({
      ...task,
      state: 'completed',
      result: {
        ...result,
        profile_projection: {
          ...result.profile_projection,
          profiles: [
            {
              ...result.profile_projection.profiles[0],
              verified_fact_ids: ['not-a-real-fact'],
            },
          ],
        },
      },
    }),
  ).toThrow('INVALID_API_RESPONSE');
});
it('fails closed when a privacy-limited profile query exposes rows or a total', () => {
  const base = {
    task_id: 't',
    batch_id: 'b',
    snapshot_id: 's',
    profile_id: 'profile-1',
    dimension_code: 'algorithm_data_structure',
    side: 'target',
    detail_status: 'privacy_limited',
    source_origin: 'deterministic_profile_replay',
    source_manifest_sha256: 'a'.repeat(64),
    verified_profile_fact_ids: ['f'],
    dimension_fact_ids: [],
    mean: { numerator: 3, denominator: 1 },
    valid_count: 24,
    total_count: null,
    offset: 0,
    limit: 10,
    has_more: false,
    items: [],
    limitation_code: 'PROFILE_SCORE_DETAIL_PRIVACY_LIMITED',
  };
  expect(aiProfileScoresDto(base).items).toEqual([]);
  expect(() => aiProfileScoresDto({ ...base, total_count: 24 })).toThrow('INVALID_API_RESPONSE');
  expect(() =>
    aiProfileScoresDto({
      ...base,
      items: [
        {
          student_id: 'student',
          evidence_id: 'e',
          dimension_code: base.dimension_code,
          score: base.mean,
          evidence_path: '/private',
        },
      ],
    }),
  ).toThrow('INVALID_API_RESPONSE');
});
