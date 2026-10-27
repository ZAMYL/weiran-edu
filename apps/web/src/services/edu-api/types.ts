import {
  count,
  checked,
  empty,
  flag,
  list,
  literal,
  nullable,
  numeric,
  object,
  text,
  type Decoded,
} from './schema';

export const DIMENSIONS = [
  { code: 'algorithm_data_structure', name: '算法与数据结构' },
  { code: 'code_implementation', name: '代码实现能力' },
  { code: 'problem_modeling', name: '问题建模能力' },
  { code: 'efficiency_awareness', name: '效率意识' },
  { code: 'solution_strategy', name: '解题策略' },
] as const;
export type DimensionCode = (typeof DIMENSIONS)[number]['code'];
export const dimensionCode = literal(...DIMENSIONS.map((d) => d.code));
const comparison = literal('lt', 'lte', 'eq', 'gte', 'gt');
export const conditionDto = object({
  dimension_code: dimensionCode,
  operator: comparison,
  value: numeric,
});
export type ApiCondition = Decoded<typeof conditionDto>;
export const pageDto = object({ offset: count, limit: count, returned: count, has_more: flag });
export const statValueDto = object({
  value: nullable(numeric),
  exact: nullable(object({ numerator: numeric, denominator: count })),
  display: text,
});
const frequencyDto = object({ score: numeric, count, denominator: count, ratio: statValueDto });
const dimensionStatsDto = object({
  dimension_code: dimensionCode,
  valid_count: count,
  missing_count: count,
  invalid_count: count,
  mean: statValueDto,
  median: statValueDto,
  minimum: statValueDto,
  maximum: statValueDto,
  distribution: list(frequencyDto),
});
const matrixDto = object({
  scope: literal('batch'),
  x_dimension: dimensionCode,
  y_dimension: dimensionCode,
  x_values: list(numeric),
  y_values: list(numeric),
  paired_count: count,
  excluded_count: count,
  cells: list(object({ x: numeric, y: numeric, count })),
});
const analysisScope = object({ kind: literal('batch', 'matched_group'), record_count: count });
const displayPolicy = object({ digits: literal(2), rounding: literal('HALF_UP') });
export const statisticsDto = object({
  batch_id: text,
  snapshot_id: text,
  rule_version: text,
  scope: analysisScope,
  display_policy: displayPolicy,
  dimensions: list(dimensionStatsDto),
  matrix: nullable(matrixDto),
});
export type ApiStatistics = Decoded<typeof statisticsDto>;
export const batchDto = object({
  batch_id: text,
  data_scope_id: text,
  snapshot_id: text,
  title: text,
  course: nullable(text),
  assessment_date: nullable(text),
  student_count: count,
  evaluation_count: count,
  rubric_version: text,
  parser_version: text,
  file_sha256: text,
  content_sha256: text,
  content_hash_version: text,
  revision_of_batch_id: nullable(text),
  is_current: flag,
  created_at: text,
});
export type ApiBatch = Decoded<typeof batchDto>;
export const batchListDto = object({ total_count: count, items: list(batchDto), page: pageDto });
export const scoreDto = object({
  dimension_code: dimensionCode,
  score: numeric,
  evidence_id: text,
});
export const studentDto = object({ student_id: text, display_label: text, scores: list(scoreDto) });
export type ApiStudent = Decoded<typeof studentDto>;
export const studentListDto = object({
  batch_id: text,
  snapshot_id: text,
  total_count: count,
  items: list(studentDto),
  page: pageDto,
});
export const groupDto = object({
  batch_id: text,
  snapshot_id: text,
  conditions: list(conditionDto),
  rule_version: text,
  matched_count: count,
  total_count: count,
  ratio: numeric,
  eligible_count: count,
  unevaluable_count: count,
  matched_among_eligible_ratio: nullable(numeric),
  items: list(studentDto),
  page: pageDto,
  statistics: nullable(statisticsDto),
});
export type ApiGroup = Decoded<typeof groupDto>;
const sourceDto = object({
  file_sha256: text,
  sheet_name: text,
  row_number: count,
  dimension_cell: text,
  score_cell: text,
  comment_cell: text,
});
export const evidenceDto = checked(
  object({
    evidence_id: text,
    batch_id: text,
    snapshot_id: text,
    student_id: text,
    dimension_code: dimensionCode,
    score: numeric,
    score_raw: text,
    comment: nullable(text),
    comment_status: literal('empty', 'withheld', 'published'),
    comment_review_version: count,
    source: sourceDto,
    rubric_criterion_id: text,
  }),
  (value) =>
    value.comment_status === 'published'
      ? value.comment !== null && value.comment.length > 0 && value.comment_review_version > 0
      : value.comment === null,
);
export type ApiEvidence = Decoded<typeof evidenceDto>;
export const studentEvidenceDto = object({
  batch_id: text,
  snapshot_id: text,
  student_id: text,
  display_label: text,
  rubric_version: text,
  items: list(evidenceDto),
});
export const rubricDto = object({
  batch_id: text,
  snapshot_id: text,
  rubric_id: text,
  rubric_version: text,
  rubric_name: text,
  score_policy: object({ minimum: numeric, maximum: numeric, step: numeric }),
  criteria: list(
    checked(
      object({
        rubric_criterion_id: text,
        dimension_code: dimensionCode,
        display_name: text,
        original_name: text,
        criteria_text: nullable(text),
        criteria_status: literal('withheld', 'published'),
        criteria_review_version: count,
        source: object({ file_sha256: text, sheet_name: text, range: text }),
      }),
      (value) =>
        value.criteria_status === 'published'
          ? value.criteria_text !== null &&
            value.criteria_text.length > 0 &&
            value.criteria_review_version > 0
          : value.criteria_text === null,
    ),
  ),
});
export type ApiRubric = Decoded<typeof rubricDto>;
const metricDto = object({
  key: text,
  value: statValueDto,
  unit: literal('records', 'score', 'ratio'),
  denominator: nullable(count),
  at_score: nullable(statValueDto),
  source_record_count: count,
});
export const cardDto = object({
  candidate_id: text,
  batch_id: text,
  snapshot_id: text,
  candidate_type: literal('dimension_distribution', 'ability_group'),
  dimension_code: nullable(dimensionCode),
  title: text,
  summary: text,
  metrics: list(metricDto),
  conditions: list(conditionDto),
  denominator: count,
  rule_id: text,
  rule_version: text,
  status: literal('observed', 'insufficient_data'),
  limitations: list(text),
  suggested_next_action: text,
  public_display_allowed: literal(false),
  public_restrictions: list(text),
});
export type InsightCard = Decoded<typeof cardDto>;
export type InsightMetric = Decoded<typeof metricDto>;
export interface InsightSpec {
  snapshot_id: string;
  conditions: ApiCondition[];
  include_dimensions: boolean;
}
export const insightsDto = object({
  batch_id: text,
  snapshot_id: text,
  audience: literal('authorized_teacher_only'),
  scope: analysisScope,
  conditions: list(conditionDto),
  include_dimensions: flag,
  rule_version: text,
  display_policy: displayPolicy,
  comment_analysis_status: literal('unavailable'),
  comment_analysis_reason: literal('reviewed_comment_provider_not_available'),
  limitations: list(text),
  total_count: count,
  items: list(cardDto),
  page: pageDto,
});
export type ApiInsights = Decoded<typeof insightsDto>;
export const recordsDto = object({
  batch_id: text,
  snapshot_id: text,
  candidate_id: text,
  metric: metricDto,
  conditions: list(conditionDto),
  rule_version: text,
  total_count: count,
  scope_record_count: count,
  items: list(studentDto),
  page: pageDto,
});
export type ApiRecords = Decoded<typeof recordsDto>;
const summaryDto = object({
  detected_student_count: count,
  complete_student_count: count,
  parsed_evaluation_count: count,
  error_count: count,
  warning_count: count,
});
const previewScoreShape = {
  dimension_code: nullable(dimensionCode),
  score: nullable(numeric),
  score_raw: nullable(text),
  score_state: literal('valid', 'invalid', 'missing'),
  comment: empty,
  comment_status: literal('withheld', 'empty'),
  source: sourceDto,
};
const previewRowDto = object({
  ...previewScoreShape,
  row_key: text,
  preview_student_id: text,
  display_label: text,
  issue_codes: list(text),
});
export const issueDto = object({
  issue_id: text,
  severity: literal('error', 'warning'),
  code: text,
  message: text,
  sheet_name: literal('Sheet1', 'Sheet2'),
  row_number: count,
  cell: text,
  row_key: nullable(text),
  row_offset: nullable(count),
  preview_student_id: nullable(text),
  display_label: nullable(text),
  dimension_code: nullable(dimensionCode),
  scope: literal('workbook', 'student'),
});
export type ApiIssue = Decoded<typeof issueDto>;
export const importDto = object({
  import: object({
    import_id: text,
    status: literal('preview_ready', 'invalid', 'committed', 'expired'),
    data_scope_id: text,
    title: text,
    course: nullable(text),
    assessment_date: nullable(text),
    revision_of_batch_id: nullable(text),
    file_sha256: text,
    content_sha256: nullable(text),
    content_hash_version: text,
    parser_version: text,
    rubric_version: nullable(text),
    preview_version: text,
    created_at: text,
    expires_at: text,
    committed_batch_id: nullable(text),
  }),
  preview_available: flag,
  summary: summaryDto,
  student_samples: nullable(
    list(
      object({
        preview_student_id: text,
        display_label: text,
        scores: list(object(previewScoreShape)),
      }),
    ),
  ),
  student_page: nullable(pageDto),
  rows: nullable(list(previewRowDto)),
  row_page: nullable(pageDto),
  issues: nullable(list(issueDto)),
  issue_page: nullable(pageDto),
});
export type ApiImport = Decoded<typeof importDto>;
export const qualityDto = object({
  report_version: text,
  parser_version: text,
  file_sha256: text,
  status: literal('passed', 'warning', 'blocked'),
  validation_passed: flag,
  requires_warning_acknowledgement: flag,
  summary: object({
    detected_student_count: count,
    complete_student_count: count,
    parsed_evaluation_count: count,
    error_count: count,
    warning_count: count,
    valid_score_count: count,
    missing_score_count: count,
    invalid_score_count: count,
    unknown_dimension_row_count: count,
    affected_student_count: count,
    students_with_errors: count,
    students_with_warnings: count,
    workbook_issue_count: count,
  }),
  dimensions: list(
    object({
      dimension_code: dimensionCode,
      dimension_label: text,
      score_row_count: count,
      valid_score_count: count,
      missing_score_count: count,
      invalid_score_count: count,
      students_missing_dimension_count: count,
      students_with_duplicate_dimension_count: count,
    }),
  ),
  issue_groups: list(
    object({
      code: text,
      severity: literal('error', 'warning'),
      message: text,
      occurrence_count: count,
      affected_student_count: count,
      affected_row_count: count,
      examples: list(issueDto),
    }),
  ),
  issues: list(issueDto),
});
export type ApiQuality = Decoded<typeof qualityDto>;
export const commitDto = object({
  import_id: text,
  batch_id: text,
  result: literal('created', 'duplicate', 'replayed'),
});
export const meDto = object({ teacher_id: text });
export const scopesDto = object({
  items: list(object({ data_scope_id: text, label: text, can_import: flag })),
});
export type ApiScope = Decoded<typeof scopesDto>['items'][number];
