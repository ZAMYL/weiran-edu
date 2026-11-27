import { describe, expect, it } from 'vitest';
import {
  phenomenonLabel,
  programmingErrorGuidance,
  programmingTaskDto,
  programmingTaskRunning,
  summarizeProgrammingResult,
} from './programming-ai-types';

const shaA = 'a'.repeat(64);
const shaB = 'b'.repeat(64);

const sampleEvidence = {
  asset_id: 'asset_1',
  source_entry: 'bundle/student1.html',
  student_key: 'S001',
  problem_label: 'A + B',
  source_index: 0,
  source_row: 3,
  test_index: 7,
};

const baseTask = {
  task_id: 'task_1',
  assessment_id: 'assess_1',
  source_snapshot_id: shaA,
  purpose: 'programming_error_investigation',
  state: 'completed',
  error_code: null,
  created_at: '2026-11-24T12:00:00Z',
  deadline_at: '2026-11-24T12:02:00Z',
  expires_at: '2026-11-24T13:00:00Z',
  execution_origin: 'http_mock',
  real_qwen: 'not_run',
  production_enabled: false,
  tool_attempts: 2,
  model_requests: 3,
  result: {
    assessment_id: 'assess_1',
    source_snapshot_id: shaA,
    asset_sha256: [shaB],
    statement: '编程调查已完成。',
    findings: [
      {
        finding_id: 'F1',
        code: 'wrong_answer',
        statement: '测试点 7：1 / 2 条覆盖记录出现该现象。根因有待核查。',
        evidence: [sampleEvidence],
      },
    ],
  },
};

describe('programming AI task contract', () => {
  it('accepts valid completed and running programming task payloads', () => {
    const completed = programmingTaskDto(baseTask);
    expect(completed.state).toBe('completed');
    expect(programmingTaskRunning(completed)).toBe(false);
    expect(completed.result?.findings[0]?.evidence[0]?.test_index).toBe(7);

    const running = programmingTaskDto({
      ...baseTask,
      state: 'running',
      result: null,
    });
    expect(programmingTaskRunning(running)).toBe(true);

    const failed = programmingTaskDto({
      ...baseTask,
      state: 'failed',
      error_code: 'AI_SOURCE_CONFLICT',
      result: null,
    });
    expect(programmingTaskRunning(failed)).toBe(false);
  });

  it('rejects mismatched snapshots, non-null results on incomplete tasks, and duplicate findings', () => {
    expect(() => programmingTaskDto({ ...baseTask, result: null })).toThrow('INVALID_API_RESPONSE');
    expect(() => programmingTaskDto({ ...baseTask, state: 'failed' })).toThrow(
      'INVALID_API_RESPONSE',
    );
    expect(() =>
      programmingTaskDto({
        ...baseTask,
        result: { ...baseTask.result, assessment_id: 'other' },
      }),
    ).toThrow('INVALID_API_RESPONSE');
    expect(() =>
      programmingTaskDto({
        ...baseTask,
        result: { ...baseTask.result, source_snapshot_id: shaB },
      }),
    ).toThrow('INVALID_API_RESPONSE');
    expect(() =>
      programmingTaskDto({
        ...baseTask,
        result: {
          ...baseTask.result,
          findings: [baseTask.result.findings[0], baseTask.result.findings[0]],
        },
      }),
    ).toThrow('INVALID_API_RESPONSE');
  });

  it('summarizes problems and matches evidence student keys without inventing missing records', () => {
    const parsed = programmingTaskDto({
      ...baseTask,
      result: {
        ...baseTask.result,
        findings: [
          ...baseTask.result.findings,
          {
            finding_id: 'F2',
            code: 'timeout',
            statement: '测试点 8：2 / 2 条覆盖记录出现该现象。根因有待核查。',
            evidence: [
              { ...sampleEvidence, problem_label: '树上路径', test_index: 8 },
              { ...sampleEvidence, student_key: 'S999', problem_label: '树上路径', test_index: 8 },
            ],
          },
        ],
      },
    });
    const summary = summarizeProgrammingResult(parsed.result!, [
      { student_pk: 'pk_1', display_name: '学生甲', external_key: 'S001' },
    ]);
    expect(summary.finding_count).toBe(2);
    expect(summary.problem_labels).toEqual(['A + B', '树上路径']);
    expect(summary.students).toEqual([
      {
        student_key: 'S001',
        student_pk: 'pk_1',
        display_name: '学生甲',
        problem_labels: ['A + B', '树上路径'],
        finding_count: 2,
        evidence_count: 2,
      },
      {
        student_key: 'S999',
        student_pk: null,
        display_name: null,
        problem_labels: ['树上路径'],
        finding_count: 1,
        evidence_count: 1,
      },
    ]);
  });

  it('maps error guidance and phenomenon labels with verbatim fallback', () => {
    expect(phenomenonLabel('wrong_answer')).toBe('答案错误');
    expect(phenomenonLabel('custom_code')).toBe('custom_code');
    expect(programmingErrorGuidance('ORIGINAL_UNAVAILABLE')?.refreshAssessment).toBe(true);
    expect(programmingErrorGuidance('UNKNOWN_CODE')).toMatchObject({
      title: '编程调查未完成',
      detail: '错误码：UNKNOWN_CODE',
    });
    expect(programmingErrorGuidance(null)).toBeNull();
  });
});
