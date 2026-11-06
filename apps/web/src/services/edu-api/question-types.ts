import {
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
import { assessmentItemDto, itemScaleDto, statValueDto } from './assessment-items';
import { exactRationalDto } from './exact';

export const QUESTION_FORMAT = 'edu-question-xlsx.v1';
export const isQuestionResponse = (value: unknown): boolean =>
  object({ format: optional(text) })(value).format === QUESTION_FORMAT;
export const qualityCountsDto = object({
  valid: optional(count),
  missing: optional(count),
  invalid_numeric: optional(count),
  out_of_range: optional(count),
  off_step: optional(count),
});
export const qualityLabels = {
  valid: '有效',
  missing: '缺分',
  invalid_numeric: '非法数值',
  out_of_range: '超出量表',
  off_step: '不符合步长',
};
export const questionPreviewDto = object({
  format: literal(QUESTION_FORMAT),
  import_id: text,
  preview_version: text,
  title: text,
  participant_count: count,
  observation_count: count,
  recognized_items: list(assessmentItemDto),
  available_analyses: list(text),
  quality_counts: qualityCountsDto,
  quality_issues: list(
    object({
      category: literal('missing', 'invalid_numeric', 'out_of_range', 'off_step'),
      count,
      sheet: text,
      cell: text,
      item_id: text,
      severity: literal('warning'),
    }),
  ),
  confirmable: flag,
});
export type QuestionPreview = Decoded<typeof questionPreviewDto>;
export function isQuestionPreview(value: unknown): value is QuestionPreview {
  return isQuestionResponse(value);
}
export const questionStatisticDto = object({
  item_id: text,
  display_name: text,
  kind: literal('question'),
  scale: nullable(itemScaleDto),
  denominator: count,
  valid_count: count,
  missing_count: count,
  invalid_count: count,
  quality_counts: qualityCountsDto,
  statistics_status: literal('available', 'unavailable'),
  mean: statValueDto,
  median: statValueDto,
  minimum: statValueDto,
  maximum: statValueDto,
  distribution: list(
    object({
      score: numeric,
      exact: exactRationalDto,
      count,
      denominator: count,
      ratio: statValueDto,
    }),
  ),
  evidence_ids: list(text),
});
export const questionStatisticsDto = object({
  format: literal(QUESTION_FORMAT),
  batch_id: text,
  snapshot_id: text,
  title: text,
  participant_count: count,
  recognized_items: list(assessmentItemDto),
  available_analyses: list(text),
  item_statistics: list(questionStatisticDto),
  analysis_status: object({
    item_statistics: literal('available', 'unavailable'),
    five_dimension_discovery: literal('unsupported'),
    evaluation_review: literal('missing_rubric_and_response'),
    cross_assessment_growth: literal('unsupported'),
  }),
});
export const questionBatchDto = object({
  format: literal(QUESTION_FORMAT),
  batch_id: text,
  snapshot_id: text,
  title: text,
  data_scope_id: optional(text),
  course: optional(nullable(text)),
  assessment_date: optional(nullable(text)),
  student_count: optional(count),
  participant_count: optional(count),
  evaluation_count: optional(count),
  recognized_items: list(assessmentItemDto),
  available_analyses: list(text),
});
export const questionEvidenceDto = object({
  format: literal(QUESTION_FORMAT),
  batch_id: text,
  snapshot_id: text,
  evidence_id: text,
  participant_id: text,
  item_id: text,
  quality_state: literal('valid', 'missing', 'invalid_numeric', 'out_of_range', 'off_step'),
  exact: nullable(exactRationalDto),
  source: object({ file_sha256: text, sheet: text, cell: text }),
});
export type QuestionEvidence = Decoded<typeof questionEvidenceDto>;
