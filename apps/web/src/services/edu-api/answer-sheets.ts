import { checked, count, flag, list, nullable, numeric, object, text } from './schema';

const snapshot = nullable(checked(text, (value) => /^[a-f0-9]{64}$/.test(value)));
const entry = {
  assessment_id: text,
  title: text,
  student_count: count,
  problem_count: count,
  review_state: text,
};
export const answerSheetImportDto = object({ assessment_id: text, duplicate: flag });
export const answerSheetListDto = object({ items: list(object(entry)), total: count });
export const answerSheetDto = object({
  ...entry,
  source_snapshot_id: snapshot,
  counts_provisional: flag,
  assets: list(object({ asset_id: text, entry: text, storage_state: text })),
  materials: list(object({ asset_id: nullable(text), entry: text, needs_confirmation: flag })),
  diagnostics: list(object({ code: text, entry: nullable(text), message: text })),
});
const student = { student_pk: text, display_name: text, external_key: text };
export const answerSheetStudentsDto = object({
  assessment_id: text,
  items: list(object({ ...student, started_at: nullable(text), finished_at: nullable(text) })),
});
export const answerSheetPatternsDto = object({
  assessment_id: text,
  items: list(
    object({
      problem_label: text,
      ordinal: count,
      submission_count: count,
      verdict_counts: list(object({ status: text, count })),
      failure_rate: nullable(numeric),
      worst_test_points: list(object({ test_index: count, failure_count: count })),
    }),
  ),
});
export const answerSheetEvidenceDto = object({
  ...student,
  assessment_id: text,
  source_snapshot_id: snapshot,
  submissions: list(
    object({
      problem_label: text,
      ordinal: count,
      verdict: nullable(text),
      language: nullable(text),
      code: text,
      code_state: text,
      code_truncated: flag,
      compiler_output: nullable(text),
      compiler_output_truncated: flag,
      source: nullable(
        object({
          asset_id: nullable(text),
          entry: nullable(text),
          source_index: nullable(count),
        }),
      ),
      observations: list(
        object({
          test_index: count,
          status: text,
          duration_text: text,
          memory_text: text,
          source_row: nullable(count),
        }),
      ),
    }),
  ),
});
