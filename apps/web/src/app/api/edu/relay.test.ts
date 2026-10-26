import { afterEach, expect, it, vi } from 'vitest';
import { GET, POST } from './[...path]/route';

const bearer = 'Bearer synthetic-test-credential-not-real';
const context = (path: string) => ({ params: Promise.resolve({ path: path.split('/') }) });
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
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
