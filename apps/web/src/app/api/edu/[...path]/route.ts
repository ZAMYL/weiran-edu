// Same-origin, fixed-upstream teacher API relay. No cookies, token storage or broad CORS.
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
const routes: Record<string, RegExp[]> = {
  GET: [
    /^(me|scopes|batches)$/,
    /^batches\/[^/]+$/,
    /^batches\/[^/]+\/(statistics|students|rubric)$/,
    /^batches\/[^/]+\/students\/[^/]+\/evidence$/,
    /^batches\/[^/]+\/evidence\/[^/]+$/,
    /^imports\/[^/]+$/,
    /^imports\/[^/]+\/quality-report$/,
  ],
  POST: [
    /^imports\/preview$/,
    /^imports\/[^/]+\/commit$/,
    /^batches\/[^/]+\/(groups|insights)\/query$/,
    /^batches\/[^/]+\/insights\/[^/]+\/records\/query$/,
  ],
  PATCH: [/^imports\/[^/]+$/],
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
]);
function fail(status: number, code: string) {
  return Response.json(
    { error: { code, message: '请求未完成' } },
    { status, headers: { 'Cache-Control': 'no-store' } },
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
  const authorization = request.headers.get('authorization');
  if (!authorization || !/^Bearer [a-zA-Z0-9_-]{20,200}$/.test(authorization))
    return fail(401, 'UNAUTHORIZED');
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
  );
  const headers = new Headers({ Authorization: authorization, Accept: 'application/json' });
  const contentType = request.headers.get('content-type');
  if (contentType) headers.set('Content-Type', contentType);
  try {
    const init: RequestInit & { duplex?: 'half' } = {
      method: request.method,
      headers,
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.any([request.signal, controller.signal]),
    };
    if (stream) {
      init.body = stream;
      init.duplex = 'half';
    }
    const response = await fetch(new URL(`/api/v1/edu/${route}${source.search}`, origin), init);
    if (!response.headers.get('content-type')?.includes('application/json'))
      return fail(502, 'INVALID_API_RESPONSE');
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
  } catch {
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
