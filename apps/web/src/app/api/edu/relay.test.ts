import { afterEach, expect, it, vi } from 'vitest';
import { GET, POST, PATCH } from './[...path]/route';

const bearer = 'Bearer synthetic-test-credential-not-real';
const context = (path: string) => ({ params: Promise.resolve({ path: path.split('/') }) });

it('scopes answer-sheet upload limits, evidence queries, and routes', async () => {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => Response.json({}));
  vi.stubGlobal('fetch', fetcher);
  const url = 'http://localhost/api/edu/';
  const headers = { authorization: bearer };
  const student = 'assessments/a/students/s';
  const snapshot = '?source_snapshot_id=' + 'a'.repeat(64);
  expect(
    (await GET(new Request(url + student + snapshot, { headers }), context(student))).status,
  ).toBe(200);
  expect(String(fetcher.mock.calls[0]?.[0])).toContain(snapshot);
  const overview = 'assessments/a';
  expect(
    (await GET(new Request(url + overview + snapshot, { headers }), context(overview))).status,
  ).toBe(403);
  const report = 'assessments/a/report';
  expect((await GET(new Request(url + report, { headers }), context(report))).status).toBe(404);
  const large = { ...headers, 'content-length': '100000' };
  expect(
    (
      await POST(
        new Request(url + 'assessments', { method: 'POST', headers: large }),
        context('assessments'),
      )
    ).status,
  ).toBe(200);
  const commit = 'imports/i/commit';
  expect(
    (await POST(new Request(url + commit, { method: 'POST', headers: large }), context(commit)))
      .status,
  ).toBe(413);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('relays live AI only with an explicit server switch and a teacher credential', async () => {
  vi.stubEnv('EDU_AI_LIVE_MODE', '1');
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ error: { code: 'AI_PURPOSE_REQUIRED' } }, { status: 403 }));
  vi.stubGlobal('fetch', fetcher);
  const route = 'batches/b/ai-investigation-tasks';
  const url = 'http://127.0.0.1:3000/api/edu/' + route;
  const run = (headers = {}) =>
    POST(new Request(url, { method: 'POST', headers, body: '{}' }), context(route));
  expect((await run()).status).toBe(401);
  expect(fetcher).not.toHaveBeenCalled();
  expect((await run({ authorization: bearer })).status).toBe(403);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it('relays the event cursor only on the exact AI events GET route', async () => {
  vi.stubEnv('EDU_AI_LIVE_MODE', '1');
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ events: [] }));
  vi.stubGlobal('fetch', fetcher);
  const path = 'batches/b/ai-investigation-tasks/t/events';
  const request = (route: string, method = 'GET') =>
    new Request(`http://127.0.0.1:3000/api/edu/${route}?snapshot_id=s&after=2`, {
      method,
      headers: { authorization: bearer },
    });
  expect((await GET(request(path), context(path))).status).toBe(200);
  expect(String(fetcher.mock.calls[0]?.[0])).toContain('snapshot_id=s&after=2');
  const status = path.replace('/events', '');
  expect((await GET(request(status), context(status))).status).toBe(403);
  expect((await POST(request(path, 'POST'), context(path))).status).toBe(404);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it.each([
  ['batches/b/discoveries/query', true],
  [`batches/b/discoveries/${'a'.repeat(24)}/query`, true],
  [`batches/b/discoveries/${'a'.repeat(24)}/records/query`, false],
  ['batches/b/analysis-results', false],
])('only forwards the diagnosis reuse session for aggregate %s', async (route, allowed) => {
  vi.stubEnv('EDU_API_ORIGIN', 'http://127.0.0.1:8136');
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ error: { code: 'SYNTHETIC_FAILURE' } }, { status: 503 }));
  vi.stubGlobal('fetch', fetcher);
  await POST(
    new Request('http://localhost/api/edu/' + route, {
      method: 'POST',
      headers: {
        authorization: bearer,
        'x-edu-diagnosis-session': '11111111-1111-4111-8111-111111111111',
      },
      body: '{}',
    }),
    context(route),
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(new Headers(fetcher.mock.lastCall![1]?.headers).has('x-edu-diagnosis-session')).toBe(
    allowed,
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it.each([
  ['POST', 'batches/b/ai-investigation-tasks'],
  ['GET', 'batches/b/ai-investigation-tasks/t'],
  ['POST', 'batches/b/ai-investigation-tasks/t/cancel'],
  ['POST', 'batches/b/ai-investigation-tasks/t/records/query'],
  ['POST', 'batches/b/ai-investigation-tasks/t/profiles/scores/query'],
  ['POST', 'batches/b/ai-investigation-tasks/t/review-submissions'],
])('gates synthetic AI %s %s behind the loopback development switches', async (method, route) => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ state: 'queued' }, { status: 202 }));
  vi.stubGlobal('fetch', fetcher);
  const run = () =>
    (method === 'GET' ? GET : POST)(
      new Request('http://127.0.0.1:3000/api/edu/' + route, {
        method,
        headers: { authorization: bearer },
        ...(method === 'POST' ? { body: '{}' } : {}),
      }),
      context(route),
    );
  expect((await run()).status).toBe(404);
  expect(fetcher).not.toHaveBeenCalled();
  vi.stubEnv('EDU_TEST_MODE', '1');
  vi.stubEnv('EDU_AI_SYNTHETIC_MODE', '1');
  expect((await run()).status).toBe(202);
  const before = fetcher.mock.calls.length;
  const wrong = route + '/unknown';
  expect(
    (
      await POST(
        new Request('http://127.0.0.1:3000/api/edu/' + wrong, {
          method: 'POST',
          headers: { authorization: bearer },
          body: '{}',
        }),
        context(wrong),
      )
    ).status,
  ).toBe(404);
  expect(fetcher).toHaveBeenCalledTimes(before);
  vi.stubEnv('EDU_API_ORIGIN', 'https://example.invalid');
  expect((await run()).status).toBe(404);
});
it('relays AI capability without opening task routes', async () => {
  vi.stubEnv('EDU_API_ORIGIN', 'http://127.0.0.1:8136');
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    Response.json(
      {
        available: false,
        mode: 'unavailable',
        reason: 'AI_LIVE_NOT_ENABLED',
        message: 'AI 分析暂不可用',
      },
      { status: 200 },
    ),
  );
  vi.stubGlobal('fetch', fetcher);
  const capability = await GET(
    new Request('http://localhost/api/edu/ai-capability', {
      headers: { authorization: bearer },
    }),
    context('ai-capability'),
  );
  expect(capability.status).toBe(200);
  expect(fetcher).toHaveBeenCalledTimes(1);
  const task = await POST(
    new Request('http://localhost/api/edu/batches/b/ai-investigation-tasks', {
      method: 'POST',
      headers: { authorization: bearer },
      body: '{}',
    }),
    context('batches/b/ai-investigation-tasks'),
  );
  expect(task.status).toBe(404);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('opens the AI task relay only with the explicit Live switch', async () => {
  vi.stubEnv('EDU_API_ORIGIN', 'http://127.0.0.1:8136');
  vi.stubEnv('EDU_AI_LIVE_MODE', '1');
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ error: { code: 'AI_LIVE_NOT_ENABLED' } }, { status: 503 }));
  vi.stubGlobal('fetch', fetcher);
  const response = await POST(
    new Request('http://localhost/api/edu/batches/b/ai-investigation-tasks', {
      method: 'POST',
      headers: { authorization: bearer },
      body: '{}',
    }),
    context('batches/b/ai-investigation-tasks'),
  );
  expect(response.status).toBe(503);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it.each([
  'batches/b/discoveries/query',
  'batches/b/discoveries/012345678901234567890123/query',
  'batches/b/discoveries/012345678901234567890123/chart/query',
  'batches/b/discoveries/012345678901234567890123/records/query',
])('relays controlled discovery POST %s and preserves failures', async (route) => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(
      Response.json(
        { error: { code: 'SNAPSHOT_MISMATCH' } },
        { status: 409, headers: { 'x-request-id': 'req_discovery' } },
      ),
    );
  vi.stubGlobal('fetch', fetcher);
  const body = JSON.stringify({ snapshot_id: 's', scope_conditions: [] });
  const response = await POST(
    new Request(`http://localhost/api/edu/${route}`, {
      method: 'POST',
      headers: { authorization: bearer, 'content-type': 'application/json' },
      body,
    }),
    context(route),
  );
  expect(response.status).toBe(409);
  expect(response.headers.get('cache-control')).toBe('no-store');
  expect(response.headers.get('x-request-id')).toBe('req_discovery');
  const [url, init] = fetcher.mock.calls[0]!;
  expect(String(url)).toBe(`http://127.0.0.1:8000/api/v1/edu/${route}`);
  expect(await new Response(init?.body).text()).toBe(body);
  const before = fetcher.mock.calls.length;
  expect(
    (
      await GET(
        new Request(`http://localhost/api/edu/${route}`, { headers: { authorization: bearer } }),
        context(route),
      )
    ).status,
  ).toBe(404);
  expect(fetcher).toHaveBeenCalledTimes(before);
});
it('relays only allowed routes to a fixed server, without cookies or permissive CORS', async () => {
  vi.stubEnv('EDU_API_ORIGIN', 'http://127.0.0.1:8136');
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    Response.json(
      { error: { code: 'SYNTHETIC_FAILURE' } },
      {
        status: 409,
        headers: {
          'x-request-id': 'req_synthetic',
          'set-cookie': 'private=1',
          'access-control-allow-origin': '*',
        },
      },
    ),
  );
  vi.stubGlobal('fetch', fetcher);
  const result = await GET(
    new Request('http://localhost/api/edu/batches/b/statistics?snapshot_id=s', {
      headers: { authorization: bearer, cookie: 'private=1', origin: 'http://localhost' },
    }),
    context('batches/b/statistics'),
  );
  expect(result.status).toBe(409);
  expect(result.headers.get('cache-control')).toBe('no-store');
  expect(result.headers.get('x-request-id')).toBe('req_synthetic');
  expect(result.headers.has('set-cookie')).toBe(false);
  expect(result.headers.has('access-control-allow-origin')).toBe(false);
  const [url, init] = fetcher.mock.calls[0]!;
  expect(String(url)).toBe('http://127.0.0.1:8136/api/v1/edu/batches/b/statistics?snapshot_id=s');
  expect(new Headers(init?.headers).has('cookie')).toBe(false);
  expect(new Headers(init?.headers).get('authorization')).toBe(bearer);
});
it('rejects unknown paths, cross-origin requests, URL credentials, and missing auth before networking', async () => {
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  for (const [route, query, headers, status] of [
    ['admin', '', { authorization: bearer }, 404],
    ['me', '', { authorization: bearer, origin: 'https://evil.invalid' }, 403],
    ['me', '?access_token=synthetic', { authorization: bearer }, 403],
    ['me', '', {}, 401],
  ] as const) {
    expect(
      (
        await GET(
          new Request(`http://localhost/api/edu/${route}${query}`, { headers }),
          context(route),
        )
      ).status,
    ).toBe(status);
  }
  expect(fetcher).not.toHaveBeenCalled();
});
it('bounds actual streamed JSON bodies, not just Content-Length', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
      await new Response(init?.body).arrayBuffer();
      return Response.json({});
    }),
  );
  const result = await POST(
    new Request('http://localhost/api/edu/batches/b/groups/query', {
      method: 'POST',
      headers: { authorization: bearer, 'content-type': 'application/json' },
      body: 'x'.repeat(65537),
    }),
    context('batches/b/groups/query'),
  );
  expect(result.status).toBe(413);
});
it('returns safe upstream failures without exposing exception text', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>().mockRejectedValue(new Error('private-host-and-key')),
  );
  const result = await GET(
    new Request('http://localhost/api/edu/me', { headers: { authorization: bearer } }),
    context('me'),
  );
  expect(result.status).toBe(503);
  expect(await result.text()).not.toContain('private-host-and-key');
});
it('accepts the actual same-origin Host when Next normalizes its internal URL', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>().mockResolvedValue(Response.json({ teacher_id: 'synthetic' })),
  );
  const result = await GET(
    new Request('http://localhost:3000/api/edu/me', {
      headers: { authorization: bearer, host: '127.0.0.1:3000', origin: 'http://127.0.0.1:3000' },
    }),
    context('me'),
  );
  expect(result.status).toBe(200);
});

it('relays report bytes and only validated attachment headers', async () => {
  const bytes = new TextEncoder().encode('# 合成报告\n');
  vi.stubGlobal(
    'fetch',
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(bytes, {
        headers: {
          'content-type': 'text/markdown; charset=utf-8',
          'content-disposition': 'attachment; filename="weiran-analysis-result-v2.md"',
          'content-length': String(bytes.length),
          'x-request-id': 'req_synthetic',
          'set-cookie': 'private=1',
        },
      }),
    ),
  );
  const result = await GET(
    new Request(
      'http://localhost/api/edu/batches/b/analysis-results/result/export?snapshot_id=s&expected_version=2&format=md',
      { headers: { authorization: bearer } },
    ),
    context('batches/b/analysis-results/result/export'),
  );
  expect(result.status).toBe(200);
  expect(result.headers.get('content-disposition')).toBe(
    'attachment; filename="weiran-analysis-result-v2.md"',
  );
  expect(result.headers.get('content-length')).toBe(String(bytes.length));
  expect(result.headers.get('x-request-id')).toBe('req_synthetic');
  expect(result.headers.has('set-cookie')).toBe(false);
  expect(await result.text()).toBe('# 合成报告\n');
});
it.each([401, 403, 409, 413, 503])(
  'preserves failed report JSON status %s without an attachment',
  async (status) => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          Response.json(
            { error: { code: 'SYNTHETIC_FAILURE' } },
            { status, headers: { 'x-request-id': 'req_synthetic' } },
          ),
        ),
    );
    const result = await GET(
      new Request(
        'http://localhost/api/edu/batches/b/analysis-results/result/export?snapshot_id=s&expected_version=2&format=xlsx',
        { headers: { authorization: bearer } },
      ),
      context('batches/b/analysis-results/result/export'),
    );
    expect(result.status).toBe(status);
    expect(result.headers.has('content-disposition')).toBe(false);
    expect(result.headers.get('x-request-id')).toBe('req_synthetic');
    expect(await result.json()).toEqual({ error: { code: 'SYNTHETIC_FAILURE' } });
  },
);
it('rejects successful JSON and unexpected attachment versions instead of downloading them', async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(Response.json({ error: {} }))
    .mockResolvedValueOnce(
      new Response('# report', {
        headers: {
          'content-type': 'text/markdown',
          'content-disposition': 'attachment; filename="weiran-analysis-result-v1.md"',
        },
      }),
    );
  vi.stubGlobal('fetch', fetcher);
  for (let index = 0; index < 2; index++) {
    const result = await GET(
      new Request(
        'http://localhost/api/edu/batches/b/analysis-results/result/export?snapshot_id=s&expected_version=2&format=md',
        { headers: { authorization: bearer } },
      ),
      context('batches/b/analysis-results/result/export'),
    );
    expect(result.status).toBe(502);
    expect(result.headers.has('content-disposition')).toBe(false);
    expect(await result.json()).toMatchObject({ error: { code: 'INVALID_REPORT_FILE' } });
  }
});
it.each(['', '&format=zip', '&format=json', '&format=md&expected_version=0'])(
  'requires an explicit supported report format and positive version: %s',
  async (query) => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const result = await GET(
      new Request(
        `http://localhost/api/edu/batches/b/analysis-results/result/export?snapshot_id=s${query}`,
        { headers: { authorization: bearer } },
      ),
      context('batches/b/analysis-results/result/export'),
    );
    expect(result.status).toBe(422);
    expect(fetcher).not.toHaveBeenCalled();
  },
);
it('allows only the required analysis and capability methods', async () => {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => Response.json({}));
  vi.stubGlobal('fetch', fetcher);
  for (const [method, route] of [
    ['GET', 'batches/b/capabilities'],
    ['GET', 'batches/b/analysis-results'],
    ['GET', 'batches/b/analysis-results/result'],
    ['POST', 'batches/b/analysis-results'],
    ['PATCH', 'batches/b/analysis-results/result'],
    ['POST', 'batches/b/analysis-results/result/export-check'],
  ] as const) {
    const handler = method === 'GET' ? GET : method === 'POST' ? POST : PATCH;
    expect(
      (
        await handler(
          new Request(`http://localhost/api/edu/${route}`, {
            method,
            headers: { authorization: bearer },
          }),
          context(route),
        )
      ).status,
    ).toBe(200);
  }
  expect(
    (
      await POST(
        new Request('http://localhost/api/edu/batches/b/analysis-results/result/export', {
          method: 'POST',
          headers: { authorization: bearer },
        }),
        context('batches/b/analysis-results/result/export'),
      )
    ).status,
  ).toBe(404);
});

it('finishes bounded JSON before an immediate upstream auth rejection, retaining 401 and request ID', async () => {
  vi.stubEnv('EDU_API_ORIGIN', 'http://127.0.0.1:8136');
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
    expect(init?.body).toBeInstanceOf(ArrayBuffer);
    expect(init && 'duplex' in init ? init.duplex : undefined).toBeUndefined();
    expect(JSON.parse(new TextDecoder().decode(init?.body as ArrayBuffer))).toEqual({
      snapshot_id: 's',
    });
    return Response.json(
      { error: { code: 'UNAUTHORIZED', request_id: 'req_auth' } },
      { status: 401, headers: { 'x-request-id': 'req_auth' } },
    );
  });
  vi.stubGlobal('fetch', fetcher);
  const response = await POST(
    new Request('http://localhost/api/edu/batches/b/discoveries/query', {
      method: 'POST',
      headers: { authorization: bearer, 'content-type': 'application/json' },
      body: JSON.stringify({ snapshot_id: 's' }),
    }),
    context('batches/b/discoveries/query'),
  );
  expect(response.status).toBe(401);
  expect(response.headers.get('x-request-id')).toBe('req_auth');
  expect(response.headers.get('cache-control')).toBe('no-store');
});

it('relays formal programming AI task routes and rejects unallowed subpaths', async () => {
  vi.stubEnv('EDU_API_ORIGIN', 'http://127.0.0.1:8136');
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    const method = init?.method ?? 'GET';
    if (method === 'POST' && String(url).endsWith('/assessments/a1/ai-tasks')) {
      return Response.json(
        { task_id: 't1', state: 'queued' },
        { status: 202, headers: { 'x-request-id': 'req_prog-start' } },
      );
    }
    if (method === 'GET' && String(url).endsWith('/assessments/a1/ai-tasks/t1')) {
      return Response.json(
        { task_id: 't1', state: 'completed' },
        { status: 200, headers: { 'x-request-id': 'req_prog-get' } },
      );
    }
    return Response.json(
      { error: { code: 'AI_TASK_CANCELLED', request_id: 'req_prog-cancel' } },
      { status: 409, headers: { 'x-request-id': 'req_prog-cancel' } },
    );
  });
  vi.stubGlobal('fetch', fetcher);

  const startRes = await POST(
    new Request('http://127.0.0.1:3000/api/edu/assessments/a1/ai-tasks', {
      method: 'POST',
      headers: { authorization: bearer, 'content-type': 'application/json' },
      body: JSON.stringify({ purpose: 'programming_error_investigation', idempotency_key: 'k1' }),
    }),
    context('assessments/a1/ai-tasks'),
  );
  expect(startRes.status).toBe(202);
  expect(startRes.headers.get('x-request-id')).toBe('req_prog-start');

  const getRes = await GET(
    new Request('http://127.0.0.1:3000/api/edu/assessments/a1/ai-tasks/t1', {
      headers: { authorization: bearer },
    }),
    context('assessments/a1/ai-tasks/t1'),
  );
  expect(getRes.status).toBe(200);
  expect(getRes.headers.get('x-request-id')).toBe('req_prog-get');

  const cancelRes = await POST(
    new Request('http://127.0.0.1:3000/api/edu/assessments/a1/ai-tasks/t1/cancel', {
      method: 'POST',
      headers: { authorization: bearer, 'content-type': 'application/json' },
      body: '{}',
    }),
    context('assessments/a1/ai-tasks/t1/cancel'),
  );
  expect(cancelRes.status).toBe(409);
  expect(cancelRes.headers.get('x-request-id')).toBe('req_prog-cancel');

  const callsBeforeReject = fetcher.mock.calls.length;
  expect(
    (
      await POST(
        new Request('http://127.0.0.1:3000/api/edu/assessments/a1/ai-tasks', {
          method: 'POST',
          body: '{}',
        }),
        context('assessments/a1/ai-tasks'),
      )
    ).status,
  ).toBe(401);
  expect(
    (
      await GET(
        new Request('http://127.0.0.1:3000/api/edu/assessments/a1/ai-tasks', {
          headers: { authorization: bearer },
        }),
        context('assessments/a1/ai-tasks'),
      )
    ).status,
  ).toBe(404);
  expect(
    (
      await POST(
        new Request('http://127.0.0.1:3000/api/edu/assessments/a1/ai-tasks/t1/unknown', {
          method: 'POST',
          headers: { authorization: bearer },
          body: '{}',
        }),
        context('assessments/a1/ai-tasks/t1/unknown'),
      )
    ).status,
  ).toBe(404);
  expect(fetcher).toHaveBeenCalledTimes(callsBeforeReject);
});

it('relays per-problem student drill-down routes with Unicode labels and rejects traversal', async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    Response.json({
      assessment_id: 'assess_1',
      problem_label: '7-1 区间求和',
      total_count: 1,
      offset: 0,
      limit: 100,
      items: [],
    }),
  );
  vi.stubGlobal('fetch', fetcher);

  const okRes = await GET(
    new Request(
      'http://127.0.0.1:3000/api/edu/assessments/assess_1/problems/7-1%20%E5%8C%BA%E9%97%B4%E6%B1%82%E5%92%8C/students?status=%E7%AD%94%E6%A1%88%E9%94%99%E8%AF%AF&test_index=7&offset=0&limit=100',
      { headers: { authorization: bearer } },
    ),
    {
      params: Promise.resolve({
        path: ['assessments', 'assess_1', 'problems', '7-1 区间求和', 'students'],
      }),
    },
  );
  expect(okRes.status).toBe(200);
  expect(fetcher).toHaveBeenCalledTimes(1);
  const calledUrl = String(fetcher.mock.calls[0]?.[0]);
  expect(calledUrl).toContain(
    '/api/v1/edu/assessments/assess_1/problems/7-1%20%E5%8C%BA%E9%97%B4%E6%B1%82%E5%92%8C/students',
  );
  expect(calledUrl).toContain('test_index=7');

  expect(
    (
      await GET(
        new Request('http://127.0.0.1:3000/api/edu/assessments/assess_1/problems/../students', {
          headers: { authorization: bearer },
        }),
        {
          params: Promise.resolve({
            path: ['assessments', 'assess_1', 'problems', '..', 'students'],
          }),
        },
      )
    ).status,
  ).toBe(404);

  expect(
    (
      await GET(
        new Request(
          'http://127.0.0.1:3000/api/edu/assessments/assess_1/problems/7-1/students?unexpected=1',
          { headers: { authorization: bearer } },
        ),
        {
          params: Promise.resolve({
            path: ['assessments', 'assess_1', 'problems', '7-1', 'students'],
          }),
        },
      )
    ).status,
  ).toBe(403);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('relays POST /api/edu/materials multipart payloads and rejects sibling paths', async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    Response.json(
      {
        recognition_id: `sha256:${'a'.repeat(64)}`,
        coverage: 'unknown',
        authority: 'none',
        materials: [
          {
            file_name: 'sample.bin',
            kind: 'csv',
            byte_size: 32,
            source_id: `sha256:${'a'.repeat(64)}`,
            status: 'recognized',
            tables: [],
            roles: [],
            fields: [],
            pending: [],
            analyses: [],
            warnings: [],
            error: null,
          },
        ],
        archives: [],
        pending: [],
      },
      { headers: { 'x-request-id': 'req_materials' } },
    ),
  );
  vi.stubGlobal('fetch', fetcher);

  const form = new FormData();
  form.append('file', new Blob(['学号,题号,源代码\nS001,7-1,int main(){}\n']), 'sample.bin');
  const res = await POST(
    new Request('http://127.0.0.1:3000/api/edu/materials', {
      method: 'POST',
      headers: { authorization: bearer },
      body: form,
    }),
    context('materials'),
  );
  expect(res.status).toBe(200);
  expect(res.headers.get('x-request-id')).toBe('req_materials');
  expect(String(fetcher.mock.calls[0]?.[0])).toBe('http://127.0.0.1:8000/api/v1/edu/materials');

  const legacy = await POST(
    new Request('http://127.0.0.1:3000/api/edu/materials/inspect', {
      method: 'POST',
      headers: { authorization: bearer },
      body: '{}',
    }),
    context('materials/inspect'),
  );
  expect(legacy.status).toBe(404);

  const bad = await POST(
    new Request('http://127.0.0.1:3000/api/edu/materials/unknown', {
      method: 'POST',
      headers: { authorization: bearer },
      body: '{}',
    }),
    context('materials/unknown'),
  );
  expect(bad.status).toBe(404);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
