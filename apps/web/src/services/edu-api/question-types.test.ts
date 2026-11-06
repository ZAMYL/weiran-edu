import { expect, it } from 'vitest';
import { batchDto } from './types';
import { questionPreviewDto, questionEvidenceDto, QUESTION_FORMAT } from './question-types';

const item = {
  item_id: 'Q01',
  display_name: '原题名',
  kind: 'question',
  scale: { minimum: '0', maximum: '10', step: '0.01', unit: '分' },
};
const preview = {
  format: QUESTION_FORMAT,
  import_id: 'i',
  preview_version: 'v',
  title: '测评',
  participant_count: 20,
  observation_count: 260,
  recognized_items: [item],
  available_analyses: ['item_statistics'],
  quality_counts: { valid: 256, missing: 1, invalid_numeric: 1, out_of_range: 1, off_step: 1 },
  quality_issues: [
    {
      category: 'missing',
      count: 1,
      sheet: 'Sheet2',
      cell: 'B3',
      item_id: 'Q01',
      severity: 'warning',
    },
  ],
  confirmable: true,
};
it('retains original decimal scales and all disjoint quality categories', () => {
  const decoded = questionPreviewDto(preview);
  expect(decoded.recognized_items[0]?.scale).toEqual(item.scale);
  expect(decoded.quality_counts).toEqual(preview.quality_counts);
  expect(decoded.quality_issues[0]?.cell).toBe('B3');
});
it('accepts both dynamic list and detail batch shapes without legacy-only metadata', () => {
  const common = {
    format: QUESTION_FORMAT,
    batch_id: 'b',
    snapshot_id: 's',
    title: '测评',
    recognized_items: [item],
    available_analyses: ['item_statistics'],
  };
  expect(batchDto({ ...common, student_count: 20, evaluation_count: 260 })).toMatchObject({
    student_count: 20,
    evaluation_count: 260,
  });
  const detail = batchDto({ ...common, participant_count: 20 });
  expect(detail.student_count).toBe(20);
  expect(detail.file_sha256).toBeUndefined();
  expect(() => batchDto(common)).toThrow('INVALID_API_RESPONSE');
});
it('rejects invalid scales and preserves the safe count requirement', () => {
  expect(() =>
    questionPreviewDto({ ...preview, participant_count: Number.MAX_SAFE_INTEGER + 1 }),
  ).toThrow('INVALID_API_RESPONSE');
  expect(() =>
    questionPreviewDto({
      ...preview,
      recognized_items: [{ ...item, scale: { ...item.scale, step: '0' } }],
    }),
  ).toThrow('INVALID_API_RESPONSE');
});
it('keeps missing evidence distinct from a valid zero score and retains the source', () => {
  const base = {
    format: QUESTION_FORMAT,
    batch_id: 'b',
    snapshot_id: 's',
    evidence_id: 'e',
    participant_id: 'S0000',
    item_id: 'Q01',
    source: { file_sha256: 'synthetic', sheet: 'Sheet2', cell: 'B2' },
  };
  expect(questionEvidenceDto({ ...base, quality_state: 'missing', exact: null }).exact).toBeNull();
  expect(
    questionEvidenceDto({
      ...base,
      quality_state: 'valid',
      exact: { numerator: 0, denominator: 1 },
    }).exact?.numerator,
  ).toBe(0);
});
