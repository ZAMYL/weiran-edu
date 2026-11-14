import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import type { AssessmentItem } from './assessment-items';
import { aiTaskDto } from './ai-task-types';
import { list, object, text } from './schema';
import { DIMENSIONS } from './types';
import { teacherAiError, teacherAiResult, teacherQuestions } from './teacher-ai-guidance';

const items: AssessmentItem[] = DIMENSIONS.map((d) => ({
  item_id: d.code,
  display_name: d.name,
  kind: 'competency',
  scale: null,
}));
const contract = list(object({ id: text, question: text, goal: text }))(
  JSON.parse(
    readFileSync(
      new URL('../../../tests/fixtures/teacher-questions.synthetic.json', import.meta.url),
      'utf8',
    ),
  ),
);
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

it('offers exact questions separately from friendly labels, matching the backend contract fixture', () => {
  expect(teacherQuestions(items).map(({ id, question }) => ({ id, question }))).toEqual(
    contract.map(({ id, question }) => ({ id, question })),
  );
  expect(teacherQuestions(items).every((q) => q.label && q.explanation)).toBe(true);
  expect(teacherQuestions([...items].reverse())).toEqual(teacherQuestions(items));
});

it('does not offer this host AI for question sheets, missing dimensions, duplicate items or unknown structures', () => {
  expect(teacherQuestions(items.slice(0, 2))).toEqual([]);
  expect(teacherQuestions(items.map((item) => ({ ...item, kind: 'question' })))).toEqual([]);
  expect(teacherQuestions([...items, items[0]!])).toEqual([]);
  expect(teacherQuestions([...items.slice(0, 4), items[0]!])).toEqual([]);
});

it('preserves unsupported input and offers an explicit choice without mapping unknown text to a supported goal', () => {
  const typed = '有什么问题';
  const hint = teacherAiError('AI_GOAL_UNSUPPORTED');
  expect(typed).toBe('有什么问题');
  expect(hint?.message).not.toMatch(/422|SQL|堆栈|unsupported/);
  expect(hint?.action).toContain('保留你的问题');
  expect(hint?.retry).toBe(false);
  expect(hint).not.toHaveProperty('question');
  expect(teacherAiError('UNRELATED_ERROR')).toBeNull();
});

it.each([
  'AI_CAMPAIGN_ALREADY_USED',
  'AI_PURPOSE_REQUIRED',
  'AI_PURPOSE_REVOKED',
  'AI_SYNTHETIC_SOURCE_REQUIRED',
])('does not suggest retrying or bypassing %s', (code) => {
  expect(teacherAiError(code)?.retry).toBe(false);
});

it.each(['queued', 'running', 'failed', 'timed_out', 'cancelled', 'revoked'])(
  'describes the actual %s state, retaining the simulated origin and no campaign restart',
  (state) => {
    const copy = teacherAiResult(aiTaskDto({ ...task, state }));
    expect(copy.origin).toBe('受控模拟分析；未调用真实 Qwen。');
    expect(copy.canStartAgain).toBe(false);
    if (!['queued', 'running'].includes(state)) expect(copy.title).toBe('本轮调查未完成');
  },
);

it.each([
  'goal_satisfied',
  'no_supported_findings',
  'needs_external_evidence',
  'bounded_scope_stop',
  'stopped_with_uncovered_directions',
  'unknown_future_reason',
])('explains stop reason %s without declaring exhaustive analysis', (stop_reason) => {
  const result = {
    execution_origin: 'http_mock',
    real_qwen: 'not_run',
    production_enabled: false,
    run_id: 'r',
    batch_id: 'b',
    snapshot_id: 's',
    status: 'completed',
    stop_reason,
    batch_record_count: 72,
    missing_evidence: [],
    facts: [],
    selected_fact_ids: [],
    retained_fact_ids: [],
    uncovered_direction_count: 2,
  };
  const copy = teacherAiResult(aiTaskDto({ ...task, state: 'completed', result }));
  expect(copy.canStartAgain).toBe(false);
  if (
    ['bounded_scope_stop', 'stopped_with_uncovered_directions', 'unknown_future_reason'].includes(
      stop_reason,
    )
  )
    expect(copy.title).toContain('仍有方向未核查');
  if (stop_reason === 'needs_external_evidence')
    expect(copy.explanation).toContain('不能解释教学原因');
  if (stop_reason === 'no_supported_findings') expect(copy.explanation).toContain('不等于没有问题');
});
