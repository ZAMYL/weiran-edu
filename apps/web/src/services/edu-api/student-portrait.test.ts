import { expect, it } from 'vitest';
import { studentPortraitRows } from './student-portrait';
import { DIMENSIONS, type ApiEvidence } from './types';

const policy = { minimum: 1, maximum: 5, step: 0.01 };
const evidence = (index: number, score = 3.25): ApiEvidence => ({
  evidence_id: `e-${index}`,
  student_id: 'student',
  batch_id: 'batch',
  snapshot_id: 'snapshot',
  dimension_code: DIMENSIONS[index]!.code,
  score,
  score_raw: String(score),
  comment: null,
  comment_status: 'withheld',
  comment_review_version: 0,
  rubric_criterion_id: 'criterion',
  source: {
    file_sha256: 'hash',
    sheet_name: 'Sheet1',
    row_number: 2,
    dimension_cell: 'A2',
    score_cell: 'B2',
    comment_cell: 'C2',
  },
});
it('uses all five individual evidence dimensions, preserving decimal scores and evidence IDs', () => {
  const rows = studentPortraitRows(
    DIMENSIONS.map((_, index) => evidence(index)),
    policy,
  );
  expect(rows.map((row) => row.score)).toEqual([3.25, 3.25, 3.25, 3.25, 3.25]);
  expect(rows.map((row) => row.evidence[0]!.evidence_id)).toEqual([
    'e-0',
    'e-1',
    'e-2',
    'e-3',
    'e-4',
  ]);
});
it('does not fill unreturned dimensions with zero', () => {
  const rows = studentPortraitRows([evidence(0)], policy);
  expect(rows[1]?.score).toBeNull();
  expect(rows[1]?.status).toBe('missing');
  expect(rows.filter((row) => row.score !== null)).toHaveLength(1);
});
it('retains multiple records for investigation without averaging or picking the first', () => {
  const rows = studentPortraitRows(
    [evidence(0, 2), { ...evidence(0, 4), evidence_id: 'other' }],
    policy,
  );
  expect(rows[0]?.status).toBe('multiple');
  expect(rows[0]?.score).toBeNull();
  expect(rows[0]?.evidence).toHaveLength(2);
});
it('does not clamp out-of-range scores or graph invalid values as zero', () => {
  for (const score of [0, 6, Infinity, NaN]) {
    const row = studentPortraitRows([evidence(0, score)], policy)[0]!;
    expect(row.status).toBe('out_of_range');
    expect(row.score).toBeNull();
    expect(row.evidence[0]?.score).toBe(score);
  }
});
