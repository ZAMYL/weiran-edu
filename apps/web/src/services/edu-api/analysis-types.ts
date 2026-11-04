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
  type Decoder,
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

import { discoveryDetailDto, discoveryScopeDto } from './discovery-types';

const optional =
  <T>(decode: Decoder<T>): Decoder<T | undefined> =>
  (value) =>
    value === undefined ? undefined : decode(value);
const optionalList =
  <T>(decode: Decoder<T>): Decoder<T[]> =>
  (value) =>
    value === undefined ? [] : list(decode)(value);
const sameConditions = (a: InsightSpec['conditions'], b: InsightSpec['conditions']) =>
  JSON.stringify(a.map((c) => [c.dimension_code, c.operator, c.value]).sort()) ===
  JSON.stringify(b.map((c) => [c.dimension_code, c.operator, c.value]).sort());
export interface DiscoverySelection {
  input_key: string;
  discovery_ids: string[];
}
const discoverySpecDto = object({
  snapshot_id: text,
  profile: literal('edu.discovery.profile.v1'),
  scope_conditions: list(conditionDto),
});
const savedDiscoveryDto = object({
  spec: discoverySpecDto,
  scope: discoveryScopeDto,
  findings: list(discoveryDetailDto),
});

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
      discovery: optional(savedDiscoveryDto),
    }),
    findings: list(object({ candidate: cardDto, teacher_review: decisionDto })),
    discovery_findings: optionalList(
      object({ discovery: discoveryDetailDto, teacher_review: decisionDto }),
    ),
  }),
  (value) =>
    (value.analysis.discovery === undefined
      ? value.discovery_findings.length === 0
      : value.analysis.query.include_dimensions === false &&
        value.findings.length === 0 &&
        value.analysis.candidates.length === 0 &&
        value.analysis.discovery.spec.snapshot_id === value.snapshot_id &&
        value.analysis.discovery.scope.batch_id === value.batch_id &&
        value.analysis.discovery.scope.snapshot_id === value.snapshot_id &&
        value.analysis.discovery.scope.scope_record_count === value.analysis.matched_count &&
        sameConditions(
          value.analysis.query.conditions,
          value.analysis.discovery.spec.scope_conditions,
        ) &&
        sameConditions(
          value.analysis.discovery.spec.scope_conditions,
          value.analysis.discovery.scope.scope_conditions,
        ) &&
        value.analysis.discovery.findings.length === value.discovery_findings.length &&
        new Set(value.discovery_findings.map((f) => f.discovery.discovery_id)).size ===
          value.discovery_findings.length &&
        value.discovery_findings.every(
          (f) =>
            f.teacher_review.candidate_id === f.discovery.discovery_id &&
            f.discovery.batch_id === value.batch_id &&
            f.discovery.snapshot_id === value.snapshot_id &&
            f.discovery.scope.input_key === value.analysis.discovery?.scope.input_key &&
            value.analysis.discovery.findings.some(
              (d) => JSON.stringify(d) === JSON.stringify(f.discovery),
            ),
        )) &&
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
  candidate_ids?: string[];
  discovery?: DiscoverySelection;
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
  pending: '待教师确认',
  confirmed: '已确认',
  dismissed: '不采纳',
};
