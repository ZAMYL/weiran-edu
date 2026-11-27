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
      full_pass_count: count,
      zero_score_count: count,
      average_score: nullable(numeric),
      verdict_counts: list(object({ status: text, count })),
      test_point_count: count,
      failure_count: count,
      failure_rate: nullable(numeric),
      worst_test_points: list(object({ test_index: count, failure_count: count })),
      pass_count: count,
      fail_count: count,
      partial_count: count,
      not_attempted_count: count,
      unknown_count: count,
      observation_count: count,
      effective_count: count,
      coverage: nullable(numeric),
      unknown_rate: nullable(numeric),
      score_rate_available: flag,
    }),
  ),
});
export const answerSheetProblemStudentsDto = object({
  assessment_id: text,
  problem_label: text,
  total_count: count,
  offset: count,
  limit: count,
  items: list(
    object({
      student_pk: text,
      external_key: text,
      display_name: text,
      verdict: nullable(text),
      score: nullable(numeric),
      score_total: nullable(numeric),
      failed_test_points: list(count),
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
