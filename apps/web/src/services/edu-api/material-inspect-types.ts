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

export const materialTableDto = object({
  ref: text,
  rows: count,
  columns: count,
  complete: flag,
  blocked: flag,
});

export const materialFieldDto = object({
  column: text,
  meaning: text,
  evidence: list(text),
});

export const materialPendingDto = object({
  code: text,
  detail: text,
});

export const materialRecognitionDto = object({
  source_id: checked(text, (value) => value.trim().length > 0),
  file_name: nullable(text),
  kind: text,
  supported: flag,
  role: nullable(text),
  role_state: literal('candidate', 'needs_confirmation', 'unsupported'),
  route: literal('material_understanding', 'assessment_import', 'unsupported'),
  tables: list(materialTableDto),
  fields: list(materialFieldDto),
  pending: list(materialPendingDto),
  analyses: list(text),
});

export type MaterialTable = Decoded<typeof materialTableDto>;
export type MaterialField = Decoded<typeof materialFieldDto>;
export type MaterialPending = Decoded<typeof materialPendingDto>;
export type MaterialRecognition = Decoded<typeof materialRecognitionDto>;

const KIND_LABELS: Record<string, string> = {
  xlsx: 'Excel 表格 (XLSX)',
  csv: 'CSV 表格',
  html: 'HTML 网页表格',
  zip_html: '编程答题卡压缩包',
  zip_other: '普通压缩包（无答题卡/表格）',
};

const ROLE_LABELS: Record<string, string> = {
  evaluation: '评分与评价数据',
  programming: '编程答题 / 测评资料',
};

const ROLE_STATE_LABELS: Record<MaterialRecognition['role_state'], string> = {
  candidate: '候选识别',
  needs_confirmation: '需教师确认',
  unsupported: '暂不支持',
};

const FIELD_MEANING_LABELS: Record<string, string> = {
  student_key: '学号 / 考生标识',
  problem_key: '题号 / 题目',
  code: '源代码',
  score: '得分',
  dimension_score: '维度评分',
  dimension: '能力维度',
  verdict: '判题结论',
  test_result: '测试点结果',
  test_point: '测试点编号',
};

const ANALYSIS_LABELS: Record<string, string> = {
  code_evidence: '代码证据',
  score_evidence: '得分证据',
  test_evidence: '测试点证据',
  five_dimensions: '五维能力评估',
  structure_linkage: '学号-题目结构关联',
};

export function materialKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind.toUpperCase();
}

export function materialRoleLabel(
  role: string | null,
  roleState: MaterialRecognition['role_state'],
): string {
  if (roleState === 'unsupported') return '未支持类型';
  if (!role) return '待确认资料角色';
  return ROLE_LABELS[role] ?? role;
}

export function materialRoleStateLabel(state: MaterialRecognition['role_state']): string {
  return ROLE_STATE_LABELS[state];
}

export function materialFieldMeaningLabel(meaning: string): string {
  return FIELD_MEANING_LABELS[meaning] ?? meaning;
}

export function materialAnalysisLabel(code: string): string {
  return ANALYSIS_LABELS[code] ?? code;
}
