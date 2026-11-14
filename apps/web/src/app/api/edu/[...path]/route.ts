// Same-origin, fixed-upstream teacher API relay.
import { testSessionAuthorization, testSessionEnabled } from '../../../../server/test-session';
import {
  readReportFile,
  ReportFileError,
  REPORT_MIME,
} from '../../../../services/edu-api/report-file';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
const routes: Record<string, RegExp[]> = {
  GET: [
    /^(me|scopes|batches|test-session)$/,
    /^batches\/[^/]+$/,
    /^batches\/[^/]+\/(statistics|students|rubric)$/,
    /^batches\/[^/]+\/(capabilities|analysis-results)$/,
    /^batches\/[^/]+\/analysis-results\/[^/]+(?:\/export)?$/,
    /^batches\/[^/]+\/students\/[^/]+\/evidence$/,
    /^batches\/[^/]+\/evidence\/[^/]+$/,
    /^imports\/[^/]+$/,
    /^imports\/[^/]+\/quality-report$/,
    /^batches\/[^/]+\/ai-investigation-tasks\/[^/]+$/,
  ],
  POST: [
    /^batches\/[^/]+\/discoveries\/query$/,
    /^batches\/[^/]+\/discoveries\/[^/]+\/query$/,
    /^batches\/[^/]+\/discoveries\/[^/]+\/(chart|records)\/query$/,
    /^imports\/preview$/,
    /^imports\/[^/]+\/commit$/,
    /^batches\/[^/]+\/(groups|insights)\/query$/,
    /^batches\/[^/]+\/insights\/[^/]+\/records\/query$/,
    /^batches\/[^/]+\/analysis-results$/,
    /^batches\/[^/]+\/analysis-results\/[^/]+\/export-check$/,
    /^batches\/[^/]+\/ai-investigation-tasks$/,
    /^batches\/[^/]+\/ai-investigation-tasks\/[^/]+\/cancel$/,
    /^batches\/[^/]+\/ai-investigation-tasks\/[^/]+\/records\/query$/,
    /^batches\/[^/]+\/ai-investigation-tasks\/[^/]+\/profiles\/scores\/query$/,
    /^batches\/[^/]+\/ai-investigation-tasks\/[^/]+\/review-submissions$/,
  ],
  PATCH: [/^imports\/[^/]+$/, /^batches\/[^/]+\/(archive|analysis-results\/[^/]+)$/],
};
const queryNames = new Set([
  'snapshot_id',
  'offset',
  'limit',
  'x_dimension',
  'y_dimension',
  'row_offset',
  'row_limit',
  'issue_offset',
  'issue_limit',
  'student_offset',
  'student_limit',
  'data_scope_id',
  'include_revisions',
  'archived',
  'include_dynamic',
  'item_id',
  'expected_version',
  'format',
]);
function fail(status: number, code: string, requestId?: string | null) {
  const safeId = requestId && /^req_[a-zA-Z0-9-]{1,100}$/.test(requestId) ? requestId : null;
  return Response.json(
    { error: { code, message: '请求未完成', ...(safeId ? { request_id: safeId } : {}) } },
    {
      status,
      headers: { 'Cache-Control': 'no-store', ...(safeId ? { 'X-Request-ID': safeId } : {}) },
    },
  );
}
async function relay(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  if (path.some((p) => !/^[a-zA-Z0-9_-]{1,128}$/.test(p))) return fail(404, 'NOT_FOUND');
  const route = path.join('/');
  if (!routes[request.method]?.some((pattern) => pattern.test(route)))
    return fail(404, 'NOT_FOUND');
  const source = new URL(request.url);
  const report = request.method === 'GET' && path[2] === 'analysis-results' && path[4] === 'export';
  const format = source.searchParams.get('format');
  const version = Number(source.searchParams.get('expected_version'));
  if (
    report &&
    ((format !== 'xlsx' && format !== 'md') ||
      !Number.isSafeInteger(version) ||
      version < 1 ||
      !source.searchParams.get('snapshot_id'))
  )
    return fail(422, 'INVALID_REPORT_REQUEST');
  // Next may normalize request.url to localhost even when the browser used 127.0.0.1.
  // Compare Origin to the actual HTTP Host; never trust forwarded host headers.
  const receivedHost = request.headers.get('host');
  const expectedOrigin =
    receivedHost && /^[a-zA-Z0-9.\[\]:-]+$/.test(receivedHost)
      ? `${source.protocol}//${receivedHost}`
      : source.origin;
  if (
    Array.from(source.searchParams.keys()).some((key) => !queryNames.has(key)) ||
    request.headers.get('sec-fetch-site') === 'cross-site' ||
    (request.headers.has('origin') && request.headers.get('origin') !== expectedOrigin)
  )
    return fail(403, 'ORIGIN_NOT_ALLOWED');
  let origin: URL;
  try {
    origin = new URL(process.env.EDU_API_ORIGIN ?? 'http://127.0.0.1:8000');
    if (
      !['http:', 'https:'].includes(origin.protocol) ||
      origin.username ||
      origin.password ||
      origin.pathname !== '/' ||
      origin.search ||
      origin.hash
    )
      return fail(503, 'BACKEND_NOT_CONFIGURED');
  } catch {
    return fail(503, 'BACKEND_NOT_CONFIGURED');
  }
  const testMode = testSessionEnabled(request, origin);
  const aiEnabled = testMode && process.env.EDU_AI_SYNTHETIC_MODE === '1';
  if (path[2] === 'ai-investigation-tasks' && !aiEnabled) return fail(404, 'NOT_FOUND');
  if (route === 'test-session')
    return Response.json(
      { enabled: testMode, ...(aiEnabled ? { ai_synthetic_enabled: true } : {}) },
      { headers: { 'Cache-Control': 'no-store' } },
    );
  let authorization = request.headers.get('authorization');
  if (!authorization && testMode) {
    try {
      authorization = await testSessionAuthorization();
    } catch {
      return fail(503, 'TEST_SESSION_NOT_CONFIGURED');
    }
  }
  if (!authorization || !/^Bearer [a-zA-Z0-9_-]{20,200}$/.test(authorization))
    return fail(401, 'UNAUTHORIZED');
  const limit = route === 'imports/preview' ? 10 * 1024 * 1024 + 64 * 1024 : 64 * 1024;
  const declaredSize = request.headers.get('content-length');
  if (declaredSize && Number(declaredSize) > limit) return fail(413, 'REQUEST_TOO_LARGE');
  let size = 0;
  const controller = new AbortController();
  const timed = setTimeout(() => controller.abort('timeout'), 40_000);
  const stream = request.body?.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, target) {
        size += chunk.byteLength;
        if (size > limit) {
          controller.abort('size');
          throw new Error('REQUEST_TOO_LARGE');
        }
        target.enqueue(chunk);
      },
    }),
    { signal: AbortSignal.any([request.signal, controller.signal]) },
  );
  const headers = new Headers({
    Authorization: authorization,
    Accept:
      report && (format === 'xlsx' || format === 'md') ? REPORT_MIME[format] : 'application/json',
  });
  const contentType = request.headers.get('content-type');
  if (contentType) headers.set('Content-Type', contentType);
  let upstreamRequestId: string | null = null;
  try {
    const init: RequestInit & { duplex?: 'half' } = {
      method: request.method,
      headers,
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.any([request.signal, controller.signal]),
    };
    if (stream) {
      // Bound and finish small JSON uploads before forwarding. A fast 401 from
      // FastAPI must not race an unfinished Node duplex upload and become 503.
      if (route === 'imports/preview') {
        init.body = stream;
        init.duplex = 'half';
      } else {
        init.body = await new Response(stream).arrayBuffer();
      }
    }
    const response = await fetch(new URL(`/api/v1/edu/${route}${source.search}`, origin), init);
    upstreamRequestId = response.headers.get('x-request-id');
    if (report && response.ok && (format === 'xlsx' || format === 'md')) {
      const file = await readReportFile(response, { resultId: path[3]!, version, format });
      const outgoing = new Headers({
        'Content-Type': format === 'md' ? 'text/markdown; charset=utf-8' : file.contentType,
        'Content-Disposition': `attachment; filename="${file.filename}"`,
        'Content-Length': String(file.bytes.byteLength),
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
      });
      const requestId = response.headers.get('x-request-id');
      if (requestId && /^req_[a-zA-Z0-9-]{1,100}$/.test(requestId))
        outgoing.set('X-Request-ID', requestId);
      return new Response(file.bytes, { status: 200, headers: outgoing });
    }
    if (!response.headers.get('content-type')?.includes('application/json'))
      return fail(502, 'INVALID_API_RESPONSE', upstreamRequestId);
    const body = await response.arrayBuffer();
    const outgoing = new Headers({
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    });
    const requestId = response.headers.get('x-request-id');
    if (requestId && /^req_[a-zA-Z0-9-]{1,100}$/.test(requestId))
      outgoing.set('X-Request-ID', requestId);
    return new Response(body, { status: response.status, headers: outgoing });
  } catch (error) {
    if (error instanceof ReportFileError) return fail(error.status, error.code, upstreamRequestId);
    return controller.signal.reason === 'size'
      ? fail(413, 'REQUEST_TOO_LARGE')
      : controller.signal.reason === 'timeout'
        ? fail(504, 'REQUEST_TIMEOUT')
        : fail(503, 'BACKEND_UNAVAILABLE');
  } finally {
    clearTimeout(timed);
  }
}
export { relay as GET, relay as POST, relay as PATCH };
