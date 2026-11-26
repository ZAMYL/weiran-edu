import {
  checked,
  ContractError,
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

export type MaterialTable = Decoded<typeof materialTableDto>;
export type MaterialField = Decoded<typeof materialFieldDto>;
export type MaterialPending = Decoded<typeof materialPendingDto>;

export type MaterialRecognition = {
  source_id: string;
  file_name: string | null;
  kind: string;
  supported: boolean;
  role: string | null;
  role_state: 'candidate' | 'needs_confirmation' | 'unsupported';
  route: 'material_understanding' | 'assessment_import' | 'unsupported';
  tables: MaterialTable[];
  fields: MaterialField[];
  pending: MaterialPending[];
  analyses: string[];
};

const nonEmptyText = checked(text, (value) => value.trim().length > 0);
const backendState = literal('confirmed', 'candidate', 'needs_confirmation', 'unsupported');

const backendColumnDto = object({
  ref: nonEmptyText,
  header: nullable(text),
  present: count,
  types: list(text),
  blocked: flag,
});

const backendTableDto = object({
  ref: nonEmptyText,
  name: nullable(text),
  rows: count,
  columns: list(backendColumnDto),
});

const backendRoleDto = object({
  table: nonEmptyText,
  role: nonEmptyText,
  state: backendState,
  reasons: list(text),
});

const backendFieldDto = object({
  column: nonEmptyText,
  header: nullable(text),
  meaning: nonEmptyText,
  state: backendState,
  reasons: list(text),
});

const backendCapabilityDto = object({
  name: nonEmptyText,
  module: text,
  table: nullable(text),
  available: flag,
  missing: list(text),
  needs_confirmation: flag,
});

const backendFileDto = object({
  file_name: text,
  kind: nonEmptyText,
  byte_size: count,
  source_id: nonEmptyText,
  status: literal('recognized', 'unsupported'),
  tables: list(backendTableDto),
  roles: list(backendRoleDto),
  fields: list(backendFieldDto),
  pending: list(text),
  analyses: list(backendCapabilityDto),
  warnings: list(text),
  error: nullable(text),
});

const backendArchiveDto = object({
  file_name: text,
  kind: nonEmptyText,
  route: nonEmptyText,
  note: text,
});

export const backendMaterialRecognitionDto = object({
  recognition_id: nonEmptyText,
  coverage: literal('unknown'),
  authority: literal('none'),
  materials: list(backendFileDto),
  archives: list(backendArchiveDto),
  pending: list(text),
});

export type BackendMaterialRecognition = Decoded<typeof backendMaterialRecognitionDto>;

const ERROR_LABELS: Record<string, string> = {
  IMPORT_FILE_INVALID: '文件内容损坏或格式与扩展名不符，服务端无法解析。',
  IMPORT_FILE_UNSUPPORTED: '当前文件类型暂不支持结构化识别。',
  IMPORT_FILE_TOO_LARGE: '文件体积超出服务端限制。',
  UNSUPPORTED_MEDIA_TYPE: '文件媒体类型不受支持。',
};

function formatBackendError(code: string): string {
  return ERROR_LABELS[code] ?? `服务端拒绝解析该文件（${code}）。`;
}

function toRoleState(state: Decoded<typeof backendState>): MaterialRecognition['role_state'] {
  if (state === 'confirmed' || state === 'candidate') return 'candidate';
  if (state === 'needs_confirmation') return 'needs_confirmation';
  return 'unsupported';
}

function buildPendingItems(file: Decoded<typeof backendFileDto>): MaterialPending[] {
  const items: MaterialPending[] = [];
  const tableNameByRef = new Map(file.tables.map((t) => [t.ref, t.name]));
  const roleByTable = new Map(file.roles.map((r) => [r.table, r]));
  const fieldByCol = new Map(file.fields.map((f) => [f.column, f]));
  const headerByCol = new Map<string, string | null>();
  for (const table of file.tables) {
    for (const col of table.columns) headerByCol.set(col.ref, col.header);
  }

  for (const ref of file.pending) {
    const field = fieldByCol.get(ref);
    if (field) {
      const label = field.header ? `${field.column}（${field.header}）` : field.column;
      items.push({
        code: 'FIELD_CONFIRMATION',
        detail: `字段 ${label} 候选含义为「${materialFieldMeaningLabel(field.meaning)}」，需教师确认`,
      });
      continue;
    }
    const role = roleByTable.get(ref);
    if (role) {
      const tName = tableNameByRef.get(ref);
      const label = tName ? `${ref}（${tName}）` : ref;
      items.push({
        code: 'ROLE_CONFIRMATION',
        detail: `数据表 ${label} 候选角色为「${materialRoleLabel(role.role, toRoleState(role.state))}」，需教师确认`,
      });
      continue;
    }
    const header = headerByCol.get(ref);
    const tName = tableNameByRef.get(ref);
    const label = header ? `${ref}（${header}）` : tName ? `${ref}（${tName}）` : ref;
    items.push({
      code: 'PENDING_CONFIRMATION',
      detail: `位置 ${label} 尚未确认角色或字段含义`,
    });
  }

  for (const warning of file.warnings) {
    items.push({ code: 'EXTRACTION_WARNING', detail: warning });
  }
  return items;
}

/**
 * 解码正式后端 POST /api/v1/edu/materials 响应，并映射为统一上传页使用的识别视图模型。
 */
export function materialRecognitionDto(raw: unknown): MaterialRecognition {
  const decoded = backendMaterialRecognitionDto(raw);

  if (decoded.archives.length > 0) {
    const archive = decoded.archives[0]!;
    const isAssessmentZip =
      archive.kind === 'zip_html' && archive.route === '/api/v1/edu/assessments';
    if (isAssessmentZip) {
      return {
        source_id: decoded.recognition_id,
        file_name: archive.file_name,
        kind: archive.kind,
        supported: true,
        role: 'programming',
        role_state: 'candidate',
        route: 'assessment_import',
        tables: [],
        fields: [],
        pending: [],
        analyses: ['code_evidence', 'test_evidence'],
      };
    }
    return {
      source_id: decoded.recognition_id,
      file_name: archive.file_name,
      kind: archive.kind,
      supported: false,
      role: null,
      role_state: 'unsupported',
      route: 'unsupported',
      tables: [],
      fields: [],
      pending: archive.note ? [{ code: 'ARCHIVE_UNSUPPORTED', detail: archive.note }] : [],
      analyses: [],
    };
  }

  if (decoded.materials.length === 0) {
    throw new ContractError();
  }

  const file = decoded.materials[0]!;
  if (file.status === 'unsupported') {
    return {
      source_id: file.source_id,
      file_name: file.file_name,
      kind: file.kind,
      supported: false,
      role: null,
      role_state: 'unsupported',
      route: 'unsupported',
      tables: [],
      fields: [],
      pending: file.error
        ? [{ code: file.error, detail: formatBackendError(file.error) }]
        : [{ code: 'UNSUPPORTED_MATERIAL', detail: '当前文件格式暂不支持识别。' }],
      analyses: [],
    };
  }

  const primaryRole =
    file.roles.find((item) => item.state === 'confirmed' || item.state === 'candidate') ??
    file.roles.find((item) => item.state === 'needs_confirmation') ??
    file.roles[0] ??
    null;

  const roleState: MaterialRecognition['role_state'] = primaryRole
    ? toRoleState(primaryRole.state)
    : 'needs_confirmation';

  const hasTruncationWarning = file.warnings.some((warning) => /truncat/i.test(warning));
  const tables: MaterialTable[] = file.tables.map((table) => {
    const blocked = table.columns.some((col) => col.blocked);
    return {
      ref: table.name ? `${table.ref}（${table.name}）` : table.ref,
      rows: table.rows,
      columns: table.columns.length,
      complete: !blocked && !hasTruncationWarning,
      blocked,
    };
  });

  const fields: MaterialField[] = file.fields.map((field) => ({
    column: field.header ? `${field.column}（${field.header}）` : field.column,
    meaning: field.meaning,
    evidence: field.reasons.length > 0 ? field.reasons : [field.column],
  }));

  const analyses = Array.from(
    new Set(
      file.analyses.filter((cap) => cap.available || cap.needs_confirmation).map((cap) => cap.name),
    ),
  );

  return {
    source_id: file.source_id,
    file_name: file.file_name,
    kind: file.kind,
    supported: true,
    role: primaryRole ? primaryRole.role : null,
    role_state: roleState,
    route: 'material_understanding',
    tables,
    fields,
    pending: buildPendingItems(file),
    analyses,
  };
}

const KIND_LABELS: Record<string, string> = {
  xlsx: 'Excel 表格 (XLSX)',
  csv: 'CSV 表格',
  html: 'HTML 网页表格',
  zip_html: '编程答题卡压缩包',
  zip_other: '普通压缩包（无答题卡/表格）',
  unknown: '未知文件类型',
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
  runtime: '运行耗时',
  rubric: '评分依据',
};

const ANALYSIS_LABELS: Record<string, string> = {
  code_evidence: '代码证据',
  score_evidence: '得分证据',
  test_evidence: '测试点证据',
  five_dimensions: '五维能力评估',
  structure_linkage: '学号-题目结构关联',
  problem_score_distribution: '题目得分分布',
  code_error_patterns: '代码错误模式分析',
  test_failure_patterns: '测试点失败模式',
  individual_submission_evidence: '个人作答证据下钻',
  five_dimension_review: '五维能力评估',
  c01_investigation: '编程错误 AI 调查',
  linked_programming_evaluation: '编程与五维关联分析',
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
