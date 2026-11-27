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

const sha256Hex = checked(text, (value) => /^[a-f0-9]{64}$/.test(value));

export const programmingEvidenceDto = object({
  asset_id: text,
  source_entry: text,
  student_key: text,
  problem_label: text,
  source_index: nullable(count),
  source_row: nullable(count),
  test_index: nullable(count),
});
export type ProgrammingEvidence = Decoded<typeof programmingEvidenceDto>;

export const programmingFindingDto = object({
  finding_id: text,
  code: text,
  statement: text,
  evidence: list(programmingEvidenceDto),
});
export type ProgrammingFinding = Decoded<typeof programmingFindingDto>;

export const programmingResultDto = checked(
  object({
    assessment_id: text,
    source_snapshot_id: sha256Hex,
    asset_sha256: list(sha256Hex),
    statement: text,
    findings: list(programmingFindingDto),
  }),
  (value) =>
    new Set(value.findings.map((finding) => finding.finding_id)).size === value.findings.length,
);
export type ProgrammingResult = Decoded<typeof programmingResultDto>;

const rawProgrammingTaskDto = object({
  task_id: text,
  assessment_id: text,
  source_snapshot_id: sha256Hex,
  purpose: literal('programming_error_investigation'),
  state: literal('queued', 'running', 'completed', 'failed', 'cancelled', 'timed_out', 'revoked'),
  error_code: nullable(text),
  created_at: text,
  deadline_at: text,
  expires_at: text,
  execution_origin: literal('http_mock', 'qwen_live', 'none'),
  real_qwen: literal('not_run', 'completed', 'failed'),
  production_enabled: flag,
  tool_attempts: count,
  model_requests: count,
  result: nullable(programmingResultDto),
});

export function programmingTaskDto(value: unknown) {
  const task = rawProgrammingTaskDto(value);
  if (task.state === 'completed') {
    if (
      task.result === null ||
      task.error_code !== null ||
      task.result.assessment_id !== task.assessment_id ||
      task.result.source_snapshot_id !== task.source_snapshot_id
    ) {
      throw new ContractError();
    }
  } else if (task.result !== null) {
    throw new ContractError();
  }
  return task;
}
export type ProgrammingTask = Decoded<typeof programmingTaskDto>;

export const programmingTaskRunning = (task: ProgrammingTask | null) =>
  !!task && (task.state === 'queued' || task.state === 'running');

export const programmingTaskStateLabels: Record<ProgrammingTask['state'], string> = {
  queued: '已排队，等待执行',
  running: '正在调查答题记录与测试点',
  completed: '调查已完成',
  failed: '调查未能完成',
  cancelled: '已停止调查',
  timed_out: '调查已超时',
  revoked: '调查授权已撤销',
};

const PHENOMENON_LABELS: Record<string, string> = {
  wrong_answer: '答案错误',
  timeout: '运行超时',
  time_limit_exceeded: '运行超时',
  memory_limit: '内存超限',
  memory_limit_exceeded: '内存超限',
  runtime_error: '运行错误',
  format_error: '输出格式错误',
  presentation_error: '输出格式错误',
  compile_error: '编译错误',
  output_limit_exceeded: '输出超限',
  partial: '部分通过',
  other_failure: '其他失败',
};

export function phenomenonLabel(code: string): string {
  return PHENOMENON_LABELS[code] ?? code;
}

export interface ProgrammingErrorGuidance {
  title: string;
  detail?: string;
  refreshAssessment?: boolean;
}

const ERROR_GUIDANCE: Record<string, ProgrammingErrorGuidance> = {
  AI_CAMPAIGN_ALREADY_USED: {
    title: '当前版本测评的编程调查机会已使用',
    detail: '同一来源快照仅允许发起一次编程 AI 调查。',
  },
  AI_LIVE_ADMISSION_REQUIRED: {
    title: '当前环境尚未开放编程 AI 调查',
    detail: '服务端未启用受控执行器或未获得独立审批与预算授权，未发起模型请求。',
  },
  AI_LIVE_NOT_ENABLED: {
    title: '当前环境未启用真实模型调查',
    detail: '可继续查看下方群体错误统计与参与者个人答题证据。',
  },
  AI_LIVE_SOURCE_NOT_APPROVED: {
    title: '该测评来源暂不在本轮授权范围内',
    detail: '请核对当前测评的审批范围后重试。',
  },
  AI_SOURCE_CONFLICT: {
    title: '测评资料或来源快照已更新，原调查结果不可继续使用',
    detail: '请刷新测评加载最新版本，再确认答题卡证据。',
    refreshAssessment: true,
  },
  AI_SOURCE_UNVERIFIED: {
    title: '当前测评缺少不可变来源快照',
    detail: '请刷新测评并核对资料来源状态。',
    refreshAssessment: true,
  },
  ORIGINAL_UNAVAILABLE: {
    title: '原始答题卡文件当前不可用，已拒绝使用缓存结果',
    detail: '请刷新测评并检查资料来源中的原件状态。',
    refreshAssessment: true,
  },
  SOURCE_CHANGED: {
    title: '资料来源已发生变化',
    detail: '请刷新测评加载最新答题卡记录。',
    refreshAssessment: true,
  },
  AI_PURPOSE_REVOKED: {
    title: '本测评的调查授权已撤销或过期',
    detail: '未保留任何未发布结论，可继续查看确定性统计与个人证据。',
  },
  AI_SCOPE_REVOKED: {
    title: '当前账号已失去该数据范围的访问权限',
  },
  AI_TASK_CANCELLED: {
    title: '已停止本次编程调查',
    detail: '任务已终止，未保存分析结果。',
  },
  AI_TASK_TIMEOUT: {
    title: '本次编程调查超时，未生成分析结果',
    detail: '当前测评仍可查看群体错误统计与个人答题证据。',
  },
  AI_TASK_EXPIRED: {
    title: '调查任务或授权已过期',
    detail: '任务结果已失效，不再展示。',
  },
  AI_PROGRAMMING_EVIDENCE_UNAVAILABLE: {
    title: '未取得可核验的编程证据',
    detail: '系统拒绝输出缺乏原件支撑的结论。',
  },
  AI_BUDGET_EXHAUSTED: {
    title: '本轮模型调用预算已用尽',
    detail: '分析未完成，未输出部分结论。',
  },
  AI_TOOL_BUDGET_EXHAUSTED: {
    title: '本轮证据核查次数已达上限',
    detail: '分析未完成，未输出部分结论。',
  },
  AI_BUDGET_AUDIT_MISMATCH: {
    title: '调查预算校验未通过',
    detail: '服务端已中止并清空未核验结果。',
  },
  AI_TASK_RESULT_INVALID: {
    title: '调查结果未通过格式与来源校验',
    detail: '已拦截不符合证据约束的输出。',
  },
  AI_TASK_FAILED: {
    title: '编程调查执行失败，未保存结果',
  },
  DATA_INTEGRITY_ERROR: {
    title: '调查结果摘要校验失败',
    detail: '请联系维护人员核查存储完整性。',
  },
};

export function programmingErrorGuidance(code: string | null): ProgrammingErrorGuidance | null {
  if (!code) return null;
  return ERROR_GUIDANCE[code] ?? { title: '编程调查未完成', detail: `错误码：${code}` };
}

export interface ProgrammingStudentRef {
  student_pk: string;
  display_name: string;
  external_key: string;
}

export interface ProgrammingStudentFocus {
  student_key: string;
  student_pk: string | null;
  display_name: string | null;
  problem_labels: string[];
  finding_count: number;
  evidence_count: number;
}

export interface ProgrammingResultSummary {
  finding_count: number;
  problem_labels: string[];
  students: ProgrammingStudentFocus[];
}

export function summarizeProgrammingResult(
  result: ProgrammingResult,
  students: readonly ProgrammingStudentRef[],
): ProgrammingResultSummary {
  const byExternalKey = new Map(students.map((student) => [student.external_key, student]));
  const problems = new Set<string>();
  const studentMap = new Map<
    string,
    {
      student_key: string;
      student_pk: string | null;
      display_name: string | null;
      problems: Set<string>;
      findingIds: Set<string>;
      evidence_count: number;
    }
  >();

  for (const finding of result.findings) {
    for (const item of finding.evidence) {
      problems.add(item.problem_label);
      const matched = byExternalKey.get(item.student_key);
      let entry = studentMap.get(item.student_key);
      if (!entry) {
        entry = {
          student_key: item.student_key,
          student_pk: matched?.student_pk ?? null,
          display_name: matched?.display_name || null,
          problems: new Set<string>(),
          findingIds: new Set<string>(),
          evidence_count: 0,
        };
        studentMap.set(item.student_key, entry);
      }
      entry.problems.add(item.problem_label);
      entry.findingIds.add(finding.finding_id);
      entry.evidence_count += 1;
    }
  }

  return {
    finding_count: result.findings.length,
    problem_labels: Array.from(problems),
    students: Array.from(studentMap.values()).map((entry) => ({
      student_key: entry.student_key,
      student_pk: entry.student_pk,
      display_name: entry.display_name,
      problem_labels: Array.from(entry.problems),
      finding_count: entry.findingIds.size,
      evidence_count: entry.evidence_count,
    })),
  };
}
