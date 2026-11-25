import { describe, expect, it } from 'vitest';
import { answerSheetPatternsDto, answerSheetProblemStudentsDto } from './answer-sheets';

describe('answerSheetPatternsDto', () => {
  it('decodes full deterministic per-problem pattern metrics', () => {
    const parsed = answerSheetPatternsDto({
      assessment_id: 'assess_1',
      items: [
        {
          problem_label: '7-1 区间求和',
          ordinal: 1,
          submission_count: 22,
          full_pass_count: 10,
          zero_score_count: 4,
          average_score: 68.5,
          verdict_counts: [
            { status: '答案错误', count: 12 },
            { status: '答案正确', count: 10 },
          ],
          test_point_count: 10,
          failure_count: 15,
          failure_rate: 0.0682,
          worst_test_points: [{ test_index: 7, failure_count: 12 }],
          pass_count: 205,
          fail_count: 15,
          partial_count: 0,
          not_attempted_count: 0,
          unknown_count: 0,
          observation_count: 220,
          effective_count: 220,
          coverage: 1,
          unknown_rate: 0,
          score_rate_available: true,
        },
      ],
    });

    expect(parsed.items).toHaveLength(1);
    expect(parsed.items[0]?.full_pass_count).toBe(10);
    expect(parsed.items[0]?.average_score).toBe(68.5);
    expect(parsed.items[0]?.score_rate_available).toBe(true);
    expect(parsed.items[0]?.coverage).toBe(1);
  });

  it('accepts nullable averages and unavailable score rate when conflicts exist', () => {
    const parsed = answerSheetPatternsDto({
      assessment_id: 'assess_2',
      items: [
        {
          problem_label: '7-2 链表反转',
          ordinal: 2,
          submission_count: 5,
          full_pass_count: 0,
          zero_score_count: 0,
          average_score: null,
          verdict_counts: [{ status: '未标注', count: 5 }],
          test_point_count: 0,
          failure_count: 0,
          failure_rate: null,
          worst_test_points: [],
          pass_count: 0,
          fail_count: 0,
          partial_count: 0,
          not_attempted_count: 0,
          unknown_count: 0,
          observation_count: 0,
          effective_count: 0,
          coverage: null,
          unknown_rate: null,
          score_rate_available: false,
        },
      ],
    });

    expect(parsed.items[0]?.average_score).toBeNull();
    expect(parsed.items[0]?.failure_rate).toBeNull();
    expect(parsed.items[0]?.coverage).toBeNull();
    expect(parsed.items[0]?.unknown_rate).toBeNull();
    expect(parsed.items[0]?.score_rate_available).toBe(false);
  });

  it('decodes per-problem student response breakdown', () => {
    const parsed = answerSheetProblemStudentsDto({
      assessment_id: 'assess_1',
      problem_label: '7-1 区间求和',
      total_count: 2,
      offset: 0,
      limit: 100,
      items: [
        {
          student_pk: 'stu_1',
          external_key: 'S001',
          display_name: '合成学生01',
          verdict: '答案错误',
          score: 0,
          score_total: 10,
          failed_test_points: [7],
        },
        {
          student_pk: 'stu_2',
          external_key: 'S002',
          display_name: '合成学生02',
          verdict: null,
          score: null,
          score_total: null,
          failed_test_points: [],
        },
      ],
    });

    expect(parsed.items).toHaveLength(2);
    expect(parsed.items[0]?.failed_test_points).toEqual([7]);
    expect(parsed.items[1]?.verdict).toBeNull();
    expect(parsed.items[1]?.score).toBeNull();
  });
});
