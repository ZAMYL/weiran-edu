import {
  checked,
  count,
  flag,
  list,
  literal,
  nullable,
  object,
  text,
  type Decoded,
} from './schema';
import {
  cardDto,
  conditionDto,
  pageDto,
  statisticsDto,
  statValueDto,
  studentDto,
  type InsightSpec,
} from './types';

const positiveVersion = checked(count, (value) => value > 0);
export const decisionStatus = literal('pending', 'confirmed', 'dismissed');
export type DecisionStatus = Decoded<typeof decisionStatus>;
export interface FindingDecision {
  candidate_id: string;
  status: DecisionStatus;
  note: string;
}
const decisionDto = object({ candidate_id: text, status: decisionStatus, note: text });
const summaryShape = {
  result_id: text,
  batch_id: text,
  snapshot_id: text,
  version: positiveVersion,
  title: text,
  created_at: text,
  updated_at: text,
};
export const analysisSummaryDto = object(summaryShape);
export type AnalysisSummary = Decoded<typeof analysisSummaryDto>;
export const analysisResultDto = checked(
  object({
    ...summaryShape,
    audience: literal('authorized_teacher_only'),
    note: text,
    reviewed_by: text,
    notice: text,
    analysis: object({
      query: object({
        snapshot_id: text,
        conditions: list(conditionDto),
        include_dimensions: flag,
      }),
      statistics: statisticsDto,
      group_statistics: nullable(statisticsDto),
      total_count: count,
      matched_count: count,
      ratio: statValueDto,
      members: list(studentDto),
      candidates: list(cardDto),
    }),
    findings: list(object({ candidate: cardDto, teacher_review: decisionDto })),
  }),
  (value) =>
    value.analysis.query.snapshot_id === value.snapshot_id &&
    value.analysis.statistics.snapshot_id === value.snapshot_id &&
    value.analysis.statistics.batch_id === value.batch_id &&
    (value.analysis.group_statistics === null ||
      (value.analysis.group_statistics.snapshot_id === value.snapshot_id &&
        value.analysis.group_statistics.batch_id === value.batch_id)) &&
    value.analysis.matched_count <= value.analysis.total_count &&
    value.analysis.members.length === value.analysis.matched_count &&
    value.analysis.candidates.every(
      (candidate) =>
        candidate.snapshot_id === value.snapshot_id && candidate.batch_id === value.batch_id,
    ) &&
    new Set(value.findings.map((finding) => finding.candidate.candidate_id)).size ===
      value.findings.length &&
    value.findings.every(
      (finding) =>
        finding.candidate.snapshot_id === value.snapshot_id &&
        finding.candidate.batch_id === value.batch_id &&
        value.analysis.candidates.some(
          (candidate) => candidate.candidate_id === finding.candidate.candidate_id,
        ) &&
        finding.teacher_review.candidate_id === finding.candidate.candidate_id,
    ),
);
export type AnalysisResult = Decoded<typeof analysisResultDto>;
export const analysisListDto = checked(
  object({
    batch_id: text,
    snapshot_id: text,
    total_count: count,
    items: list(analysisSummaryDto),
    page: pageDto,
  }),
  (value) =>
    value.items.every(
      (item) => item.batch_id === value.batch_id && item.snapshot_id === value.snapshot_id,
    ),
);
export interface AnalysisCreate extends InsightSpec {
  title: string;
  note: string;
}
export interface AnalysisPatch {
  snapshot_id: string;
  expected_version: number;
  title: string;
  note: string;
  decisions: FindingDecision[];
}
export const capabilitiesDto = object({
  batch_id: text,
  snapshot_id: text,
  role: literal('owner', 'reader'),
  operations: list(
    object({
      action: literal(
        'read_batch',
        'import_data',
        'diagnose_integrity',
        'review_text',
        'save_analysis',
        'edit_analysis',
        'export_analysis',
      ),
      available: flag,
      allowed: flag,
      reason: literal(
        'allowed',
        'owner_required',
        'feature_unavailable',
        'import_permission_required',
      ),
    }),
  ),
});
export type Capabilities = Decoded<typeof capabilitiesDto>;
export const allowed = (
  capabilities: Capabilities | null | undefined,
  action: Capabilities['operations'][number]['action'],
) =>
  Boolean(
    capabilities?.operations.some(
      (operation) => operation.action === action && operation.available && operation.allowed,
    ),
  );
export type ReportFormat = 'xlsx' | 'md';
export const exportCheckDto = object({
  batch_id: text,
  snapshot_id: text,
  result_id: text,
  result_version: positiveVersion,
  format: literal('xlsx', 'md'),
  exportable: literal(true),
  member_count: count,
  confirmed_finding_count: count,
  json_payload_bytes: count,
  max_json_payload_bytes: count,
  report_payload_bytes: count,
  max_report_payload_bytes: count,
  warnings: list(
    object({
      code: literal(
        'PENDING_FINDINGS_EXCLUDED',
        'DISMISSED_FINDINGS_EXCLUDED',
        'WITHHELD_COMMENTS_EXCLUDED',
        'WITHHELD_RUBRIC_EXCLUDED',
      ),
      count,
      message: text,
    }),
  ),
  note: text,
});
export type ExportCheck = Decoded<typeof exportCheckDto>;
export const decisionLabels: Record<DecisionStatus, string> = {
  pending: '待定',
  confirmed: '确认',
  dismissed: '否决',
};
