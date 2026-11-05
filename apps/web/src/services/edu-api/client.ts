import { ContractError, type Decoder } from './schema';
import {
  discoveriesDto,
  discoveryDetailDto,
  discoveryChartDto,
  discoveryRecordsDto,
  type DiscoverySpec,
} from './discovery-types';
import {
  analysisListDto,
  analysisResultDto,
  capabilitiesDto,
  exportCheckDto,
  type AnalysisCreate,
  type AnalysisPatch,
  type ReportFormat,
} from './analysis-types';
import { readReportFile, ReportFileError, type ReportFile } from './report-file';
import { aiTaskDto, aiSourcesDto, aiReviewDto, type AiTask } from './ai-task-types';
import {
  isQuestionResponse,
  questionPreviewDto,
  questionStatisticsDto,
  questionEvidenceDto,
  type QuestionPreview,
} from './question-types';

import {
  assessmentStatisticsDto,
  batchDto,
  batchListDto,
  commitDto,
  evidenceDto,
  groupDto,
  importDto,
  insightsDto,
  meDto,
  qualityDto,
  recordsDto,
  rubricDto,
  scopesDto,
  statisticsDto,
  studentEvidenceDto,
  studentListDto,
  type ApiBatch,
  type ApiImport,
  type ApiCondition,
  type InsightSpec,
} from './types';

const STATUS_MESSAGES: Record<number, string> = {
  401: '教师凭证已失效或被撤销，请重新连接。',
  403: '当前教师没有执行此操作的权限。',
  404: '记录不存在或当前教师无权查看，请刷新批次。',
  409: '数据版本或操作状态已变化，请刷新后重试。',
  410: '导入预览已过期，请重新上传。',
  413: '文件或查询结果超过服务限制，请缩小范围。',
  415: '文件或请求格式不受支持。',
  422: '文件或筛选参数不符合当前接口要求，请检查后重试。',
  503: '数据服务暂不可用或繁忙，请稍后重试。',
  504: '请求超时，请检查连接后重试。',
};
const FILE_MESSAGES: Record<string, string> = {
  AI_GOAL_UNSUPPORTED:
    '当前合成服务不支持这个问题，请使用优先核查、评分关联或群体差异等已支持的问法。',
  AI_PURPOSE_REQUIRED: '这份测评尚未获得合成 AI 分析授权。',
  AI_PURPOSE_REVOKED: '本测评的 AI 分析授权已撤销。',
  IMPORT_FORMAT_UNSUPPORTED: '当前暂不支持此文件结构，未创建测评批次。请核对文件结构后重新选择。',
  IMPORT_SCOPE_REQUIRED: '请选择具有导入权限的数据来源后继续检查。',
  IMPORT_FILE_INVALID:
    '当前无法识别此工作簿的结构，或文件触及解析限制。请保留原文件并核对检查要求。',
  IMPORT_FILE_TOO_LARGE: '上传文件超过当前大小限制，请检查文件后重试。',
  IMPORT_PROCESSING_UNAVAILABLE: '文件检查服务暂时繁忙或不可用，请稍后重试。',
  INVALID_PRINTER_SETTINGS_PACKAGE:
    '文件中的打印设置部件或引用不一致，无法安全读取；原文件未被修改。',
  UNSUPPORTED_PRINTER_SETTINGS_FORMAT: '文件中的打印设置格式暂不受支持；原文件未被修改。',
  PRINTER_SETTINGS_SIZE_LIMIT: '文件中的打印设置超过安全大小或数量限制；原文件未被修改。',
  INVALID_WORKBOOK_RELATIONSHIP: '工作簿内部引用异常，无法确定安全来源；原文件未被修改。',
};
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly requestId: string | null = null,
  ) {
    super(
      FILE_MESSAGES[code] ??
        (code === 'SNAPSHOT_MISMATCH'
          ? '批次快照已变化，请刷新批次后重新查询。'
          : code === 'ANALYSIS_RESULT_VERSION_MISMATCH'
            ? '分析的核验版本已更新，请加载最新结果后重新操作。'
            : code === 'INVALID_REPORT_FILE'
              ? '报告响应格式异常，已停止下载，请重试或联系维护人员。'
              : (STATUS_MESSAGES[status] ??
                (status === 0
                  ? '无法连接数据服务，请检查本机服务和网络后重试。'
                  : '请求未完成，请重试。'))),
    );
  }
}
const safeCode = (value: unknown) =>
  typeof value === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/.test(value) ? value : 'REQUEST_FAILED';
const safeRequestId = (value: unknown) =>
  typeof value === 'string' && /^req_[a-zA-Z0-9-]{1,100}$/.test(value) ? value : null;
const id = encodeURIComponent;
export function assertSnapshot<T extends { batch_id: string; snapshot_id: string }>(
  value: T,
  batch: ApiBatch,
): T {
  if (value.batch_id !== batch.batch_id || value.snapshot_id !== batch.snapshot_id)
    throw new ApiError(409, 'SNAPSHOT_MISMATCH');
  return value;
}

// Bearer lives only in this closure, with no persistence, logging or public state field.
export function createEduClient(onUnauthorized: () => void, transport: typeof fetch = fetch) {
  let bearer: string | null = null;
  let testSession = false;
  let generation = 0;
  const pending = new Set<AbortController>();
  function clear() {
    generation++;
    bearer = null;
    testSession = false;
    pending.forEach((c) => c.abort());
    pending.clear();
  }
  function authenticate(token: string) {
    clear();
    bearer = token;
  }
  async function request<T>(
    path: string,
    decode: Decoder<T>,
    init: RequestInit = {},
    signal?: AbortSignal,
    readResponse?: (response: Response) => Promise<T>,
  ): Promise<T> {
    if (!bearer && !testSession) throw new ApiError(401, 'UNAUTHORIZED');
    const epoch = generation;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort('timeout'), 45_000);
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) controller.abort();
    pending.add(controller);
    try {
      const headers = new Headers(init.headers);
      if (bearer) headers.set('Authorization', `Bearer ${bearer}`);
      if (typeof init.body === 'string') headers.set('Content-Type', 'application/json');
      const response = await transport(`/api/edu${path}`, {
        ...init,
        headers,
        signal: controller.signal,
        cache: 'no-store',
        credentials: 'omit',
        redirect: 'error',
      });
      if (epoch !== generation || signal?.aborted || controller.signal.aborted)
        throw new DOMException('Aborted', 'AbortError');
      const payload: unknown =
        readResponse && response.ok ? null : await response.json().catch(() => null);
      if (epoch !== generation || signal?.aborted || controller.signal.aborted)
        throw new DOMException('Aborted', 'AbortError');
      if (!response.ok) {
        const error =
          payload && typeof payload === 'object' && 'error' in payload ? payload.error : null;
        const details =
          error && typeof error === 'object' ? (error as Record<string, unknown>) : {};
        const failure = new ApiError(
          response.status,
          safeCode(details.code),
          safeRequestId(details.request_id ?? response.headers.get('x-request-id')),
        );
        if (response.status === 401 && epoch === generation) {
          clear();
          onUnauthorized();
        }
        throw failure;
      }
      try {
        const value = readResponse ? await readResponse(response) : decode(payload);
        if (epoch !== generation || signal?.aborted || controller.signal.aborted)
          throw new DOMException('Aborted', 'AbortError');
        return value;
      } catch (e) {
        if (e instanceof ReportFileError)
          throw new ApiError(e.status, e.code, safeRequestId(response.headers.get('x-request-id')));
        if (e instanceof ContractError)
          throw new ApiError(
            502,
            'INVALID_API_RESPONSE',
            safeRequestId(response.headers.get('x-request-id')),
          );
        throw e;
      }
    } catch (e) {
      if (e instanceof ApiError) throw e;
      if (
        epoch !== generation ||
        signal?.aborted ||
        (controller.signal.aborted && controller.signal.reason !== 'timeout')
      )
        throw new DOMException('Aborted', 'AbortError');
      throw new ApiError(
        controller.signal.reason === 'timeout' ? 504 : 0,
        controller.signal.reason === 'timeout' ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR',
      );
    } finally {
      clearTimeout(timer);
      pending.delete(controller);
      signal?.removeEventListener('abort', abort);
    }
  }
  const get = <T>(path: string, decoder: Decoder<T>, signal?: AbortSignal) =>
    request(path, decoder, {}, signal);
  const post = <T>(path: string, decoder: Decoder<T>, body: unknown, signal?: AbortSignal) =>
    request(path, decoder, { method: 'POST', body: JSON.stringify(body) }, signal);
  const base = (b: ApiBatch) => `/batches/${id(b.batch_id)}`;
  const snapshot = (b: ApiBatch) => `snapshot_id=${id(b.snapshot_id)}`;
  const discovery = async <
    T extends {
      scope: { batch_id: string; snapshot_id: string; scope_conditions: ApiCondition[] };
    },
  >(
    batch: ApiBatch,
    spec: DiscoverySpec,
    suffix: string,
    decoder: Decoder<T>,
    body: unknown,
    signal?: AbortSignal,
  ) => {
    const result = await post(`${base(batch)}/discoveries/${suffix}`, decoder, body, signal);
    assertSnapshot(result.scope, batch);
    const canonical = (conditions: ApiCondition[]) =>
      JSON.stringify(
        [...conditions]
          .sort((a, b) => a.dimension_code.localeCompare(b.dimension_code))
          .map((c) => [c.dimension_code, c.operator, c.value]),
      );
    if (canonical(result.scope.scope_conditions) !== canonical(spec.scope_conditions))
      throw new ApiError(409, 'SNAPSHOT_MISMATCH');
    return result;
  };
  return {
    startAiTask: async (batch: ApiBatch, question: string, key: string, signal?: AbortSignal) =>
      assertSnapshot(
        await post(
          `${base(batch)}/ai-investigation-tasks`,
          aiTaskDto,
          { snapshot_id: batch.snapshot_id, question, idempotency_key: key },
          signal,
        ),
        batch,
      ),
    aiTask: async (batch: ApiBatch, taskId: string, signal?: AbortSignal) => {
      const task = assertSnapshot(
        await get(
          `${base(batch)}/ai-investigation-tasks/${id(taskId)}?${snapshot(batch)}`,
          aiTaskDto,
          signal,
        ),
        batch,
      );
      if (task.task_id !== taskId) throw new ApiError(502, 'INVALID_API_RESPONSE');
      return task;
    },
    cancelAiTask: async (batch: ApiBatch, taskId: string, signal?: AbortSignal) => {
      const task = assertSnapshot(
        await post(
          `${base(batch)}/ai-investigation-tasks/${id(taskId)}/cancel?${snapshot(batch)}`,
          aiTaskDto,
          {},
          signal,
        ),
        batch,
      );
      if (task.task_id !== taskId) throw new ApiError(502, 'INVALID_API_RESPONSE');
      return task;
    },
    aiSources: async (
      batch: ApiBatch,
      task: AiTask,
      factId: string,
      metricKey: string,
      offset: number,
      signal?: AbortSignal,
    ) => {
      const value = assertSnapshot(
        await post(
          `${base(batch)}/ai-investigation-tasks/${id(task.task_id)}/records/query`,
          aiSourcesDto,
          {
            snapshot_id: batch.snapshot_id,
            fact_id: factId,
            metric_key: metricKey,
            offset,
            limit: 10,
          },
          signal,
        ),
        batch,
      );
      if (
        value.run_id !== task.result?.run_id ||
        value.fact_id !== factId ||
        value.metric_key !== metricKey ||
        value.offset !== offset
      )
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      return value;
    },
    submitAiReview: async (
      batch: ApiBatch,
      taskId: string,
      factIds: string[],
      signal?: AbortSignal,
    ) => {
      const receipt = assertSnapshot(
        await post(
          `${base(batch)}/ai-investigation-tasks/${id(taskId)}/review-submissions`,
          aiReviewDto,
          { snapshot_id: batch.snapshot_id, fact_ids: factIds },
          signal,
        ),
        batch,
      );
      if (receipt.task_id !== taskId) throw new ApiError(502, 'INVALID_API_RESPONSE');
      return receipt;
    },
    discoveries: (batch: ApiBatch, spec: DiscoverySpec, offset = 0, signal?: AbortSignal) =>
      discovery(batch, spec, 'query', discoveriesDto, { ...spec, offset, limit: 10 }, signal),
    discoveryDetail: async (
      batch: ApiBatch,
      discoveryId: string,
      spec: DiscoverySpec,
      signal?: AbortSignal,
    ) => {
      const value = await discovery(
        batch,
        spec,
        `${id(discoveryId)}/query`,
        discoveryDetailDto,
        spec,
        signal,
      );
      if (value.discovery_id !== discoveryId) throw new ApiError(502, 'INVALID_API_RESPONSE');
      return value;
    },
    discoveryChart: async (
      batch: ApiBatch,
      discoveryId: string,
      spec: DiscoverySpec,
      signal?: AbortSignal,
    ) => {
      const value = await discovery(
        batch,
        spec,
        `${id(discoveryId)}/chart/query`,
        discoveryChartDto,
        spec,
        signal,
      );
      if (value.discovery_id !== discoveryId) throw new ApiError(502, 'INVALID_API_RESPONSE');
      return value;
    },
    discoveryRecords: async (
      batch: ApiBatch,
      discoveryId: string,
      metricKey: string,
      spec: DiscoverySpec,
      offset = 0,
      signal?: AbortSignal,
    ) => {
      const value = await discovery(
        batch,
        spec,
        `${id(discoveryId)}/records/query`,
        discoveryRecordsDto,
        { ...spec, metric_key: metricKey, offset, limit: 10 },
        signal,
      );
      if (value.discovery_id !== discoveryId || value.metric_key !== metricKey)
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      return value;
    },
    clear,
    authenticate,
    useTestSession: () => {
      clear();
      testSession = true;
    },
    me: (signal?: AbortSignal) => get('/me', meDto, signal),
    scopes: (signal?: AbortSignal) => get('/scopes', scopesDto, signal),
    batches: (offset = 0, limit = 20, signal?: AbortSignal) =>
      get(`/batches?offset=${offset}&limit=${limit}&include_dynamic=true`, batchListDto, signal),
    batch: (batchId: string, signal?: AbortSignal) =>
      get(`/batches/${id(batchId)}`, batchDto, signal),
    itemStatistics: async (batch: ApiBatch, signal?: AbortSignal, itemId?: string) => {
      const result = assertSnapshot(
        await get(
          `${base(batch)}/statistics?${snapshot(batch)}${itemId ? '&item_id=' + id(itemId) : ''}`,
          (value) => (batch.format ? questionStatisticsDto(value) : assessmentStatisticsDto(value)),
          signal,
        ),
        batch,
      );
      const ids = new Set(
        batch.recognized_items
          ?.filter((item) => !itemId || item.item_id === itemId)
          .map((item) => item.item_id),
      );
      for (const item of result.item_statistics) {
        if (!ids.delete(item.item_id)) throw new ApiError(502, 'INVALID_API_RESPONSE');
      }
      if (ids.size) throw new ApiError(502, 'INVALID_API_RESPONSE');
      return result;
    },
    itemEvidence: async (
      batch: ApiBatch,
      evidenceId: string,
      itemId: string,
      signal?: AbortSignal,
    ) => {
      const value = assertSnapshot(
        await get(
          `${base(batch)}/evidence/${id(evidenceId)}?${snapshot(batch)}`,
          questionEvidenceDto,
          signal,
        ),
        batch,
      );
      if (value.evidence_id !== evidenceId || value.item_id !== itemId)
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      return value;
    },
    statistics: async (
      batch: ApiBatch,
      x = 'algorithm_data_structure',
      y = 'solution_strategy',
      signal?: AbortSignal,
    ) =>
      assertSnapshot(
        await get(
          `${base(batch)}/statistics?${snapshot(batch)}&x_dimension=${id(x)}&y_dimension=${id(y)}`,
          statisticsDto,
          signal,
        ),
        batch,
      ),
    rubric: async (batch: ApiBatch, signal?: AbortSignal) =>
      assertSnapshot(
        await get(`${base(batch)}/rubric?${snapshot(batch)}`, rubricDto, signal),
        batch,
      ),
    students: async (batch: ApiBatch, offset: number, signal?: AbortSignal) =>
      assertSnapshot(
        await get(
          `${base(batch)}/students?${snapshot(batch)}&offset=${offset}&limit=10`,
          studentListDto,
          signal,
        ),
        batch,
      ),
    group: async (
      batch: ApiBatch,
      conditions: ApiCondition[],
      offset: number,
      signal?: AbortSignal,
    ) =>
      assertSnapshot(
        await post(
          `${base(batch)}/groups/query`,
          groupDto,
          {
            snapshot_id: batch.snapshot_id,
            conditions,
            include_statistics: true,
            offset,
            limit: 10,
          },
          signal,
        ),
        batch,
      ),
    studentEvidence: async (batch: ApiBatch, studentId: string, signal?: AbortSignal) =>
      assertSnapshot(
        await get(
          `${base(batch)}/students/${id(studentId)}/evidence?${snapshot(batch)}`,
          studentEvidenceDto,
          signal,
        ),
        batch,
      ),
    evidence: async (batch: ApiBatch, evidenceId: string, signal?: AbortSignal) =>
      assertSnapshot(
        await get(
          `${base(batch)}/evidence/${id(evidenceId)}?${snapshot(batch)}`,
          evidenceDto,
          signal,
        ),
        batch,
      ),
    insights: async (batch: ApiBatch, spec: InsightSpec, offset: number, signal?: AbortSignal) =>
      assertSnapshot(
        await post(
          `${base(batch)}/insights/query`,
          insightsDto,
          { ...spec, offset, limit: 10 },
          signal,
        ),
        batch,
      ),
    records: async (
      batch: ApiBatch,
      candidateId: string,
      metricKey: string,
      spec: InsightSpec,
      offset: number,
      signal?: AbortSignal,
    ) =>
      assertSnapshot(
        await post(
          `${base(batch)}/insights/${id(candidateId)}/records/query`,
          recordsDto,
          { ...spec, metric_key: metricKey, offset, limit: 10 },
          signal,
        ),
        batch,
      ),
    capabilities: async (batch: ApiBatch, signal?: AbortSignal) =>
      assertSnapshot(
        await get(`${base(batch)}/capabilities?${snapshot(batch)}`, capabilitiesDto, signal),
        batch,
      ),
    analyses: async (batch: ApiBatch, offset: number, signal?: AbortSignal) =>
      assertSnapshot(
        await get(
          `${base(batch)}/analysis-results?${snapshot(batch)}&offset=${offset}&limit=10`,
          analysisListDto,
          signal,
        ),
        batch,
      ),
    createAnalysis: async (batch: ApiBatch, body: AnalysisCreate, signal?: AbortSignal) =>
      assertSnapshot(
        await post(`${base(batch)}/analysis-results`, analysisResultDto, body, signal),
        batch,
      ),
    analysis: async (batch: ApiBatch, resultId: string, signal?: AbortSignal) => {
      const value = assertSnapshot(
        await get(
          `${base(batch)}/analysis-results/${id(resultId)}?${snapshot(batch)}`,
          analysisResultDto,
          signal,
        ),
        batch,
      );
      if (value.result_id !== resultId) throw new ApiError(502, 'INVALID_API_RESPONSE');
      return value;
    },
    patchAnalysis: async (
      batch: ApiBatch,
      resultId: string,
      body: AnalysisPatch,
      signal?: AbortSignal,
    ) => {
      const value = assertSnapshot(
        await request(
          `${base(batch)}/analysis-results/${id(resultId)}`,
          analysisResultDto,
          { method: 'PATCH', body: JSON.stringify(body) },
          signal,
        ),
        batch,
      );
      if (value.result_id !== resultId) throw new ApiError(502, 'INVALID_API_RESPONSE');
      return value;
    },
    exportCheck: async (
      batch: ApiBatch,
      resultId: string,
      version: number,
      format: ReportFormat,
      signal?: AbortSignal,
    ) => {
      const value = assertSnapshot(
        await post(
          `${base(batch)}/analysis-results/${id(resultId)}/export-check`,
          exportCheckDto,
          { snapshot_id: batch.snapshot_id, expected_version: version, format },
          signal,
        ),
        batch,
      );
      if (
        value.result_id !== resultId ||
        value.result_version !== version ||
        value.format !== format
      )
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      return value;
    },
    report: (
      batch: ApiBatch,
      resultId: string,
      version: number,
      format: ReportFormat,
      signal?: AbortSignal,
    ) =>
      request<ReportFile>(
        `${base(batch)}/analysis-results/${id(resultId)}/export?${snapshot(batch)}&expected_version=${version}&format=${format}`,
        () => {
          throw new ContractError();
        },
        {},
        signal,
        (response) => readReportFile(response, { resultId, version, format }),
      ),
    preview: (form: FormData, signal?: AbortSignal) =>
      request<ApiImport | QuestionPreview>(
        '/imports/preview',
        (value) => (isQuestionResponse(value) ? questionPreviewDto(value) : importDto(value)),
        { method: 'POST', body: form },
        signal,
      ),
    questionImport: (importId: string, signal?: AbortSignal) =>
      get(`/imports/${id(importId)}/quality-report`, questionPreviewDto, signal),
    patchQuestionImport: (
      importId: string,
      previewVersion: string,
      title: string,
      signal?: AbortSignal,
    ) =>
      request(
        `/imports/${id(importId)}`,
        questionPreviewDto,
        { method: 'PATCH', body: JSON.stringify({ preview_version: previewVersion, title }) },
        signal,
      ),
    import: (importId: string, rowOffset = 0, issueOffset = 0, signal?: AbortSignal) =>
      get(
        `/imports/${id(importId)}?row_offset=${rowOffset}&row_limit=10&issue_offset=${issueOffset}&issue_limit=10`,
        importDto,
        signal,
      ),
    patchImport: (importId: string, previewVersion: string, title: string, signal?: AbortSignal) =>
      request(
        `/imports/${id(importId)}`,
        importDto,
        { method: 'PATCH', body: JSON.stringify({ preview_version: previewVersion, title }) },
        signal,
      ),
    quality: (importId: string, signal?: AbortSignal) =>
      get(`/imports/${id(importId)}/quality-report`, qualityDto, signal),
    commit: (
      importId: string,
      previewVersion: string,
      acknowledge: boolean,
      signal?: AbortSignal,
    ) =>
      post(
        `/imports/${id(importId)}/commit`,
        commitDto,
        { preview_version: previewVersion, acknowledge_warnings: acknowledge },
        signal,
      ),
  };
}
export type EduClient = ReturnType<typeof createEduClient>;
