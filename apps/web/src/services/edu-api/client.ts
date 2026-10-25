import { ContractError, type Decoder } from './schema';
import {
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
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly requestId: string | null = null,
  ) {
    super(
      code === 'SNAPSHOT_MISMATCH'
        ? '批次快照已变化，请刷新批次后重新查询。'
        : (STATUS_MESSAGES[status] ??
            (status === 0
              ? '无法连接数据服务，请检查本机服务和网络后重试。'
              : '请求未完成，请重试。')),
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
      const payload: unknown = await response.json().catch(() => null);
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
        return decode(payload);
      } catch (e) {
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
  return {
    clear,
    authenticate,
    useTestSession: () => {
      clear();
      testSession = true;
    },
    me: (signal?: AbortSignal) => get('/me', meDto, signal),
    scopes: (signal?: AbortSignal) => get('/scopes', scopesDto, signal),
    batches: (offset = 0, limit = 20, signal?: AbortSignal) =>
      get(`/batches?offset=${offset}&limit=${limit}`, batchListDto, signal),
    batch: (batchId: string, signal?: AbortSignal) =>
      get(`/batches/${id(batchId)}`, batchDto, signal),
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
    preview: (form: FormData, signal?: AbortSignal) =>
      request('/imports/preview', importDto, { method: 'POST', body: form }, signal),
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
