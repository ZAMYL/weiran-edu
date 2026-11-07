import { expect, it } from 'vitest';
import { aiTaskDto, aiRunning, stopLabels } from './ai-task-types';
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
