import { readFile, stat } from 'node:fs/promises';
import type { Stats } from 'node:fs';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { GET, POST } from './[...path]/route';

vi.mock('node:fs/promises', () => ({ readFile: vi.fn(), stat: vi.fn() }));
const token = 'synthetic-local-teacher-not-a-real-token';
const context = (path: string) => ({ params: Promise.resolve({ path: path.split('/') }) });
const localFile = process.platform === 'win32' ? 'C:/test/session.json' : '/test/session.json';
beforeEach(() => {
  vi.stubEnv('EDU_TEST_MODE', '1');
  vi.stubEnv('EDU_TEST_SESSION_FILE', localFile);
  vi.stubEnv('EDU_API_ORIGIN', 'http://127.0.0.1:8136');
  vi.mocked(stat).mockResolvedValue({ size: 100 } as Stats);
  vi.mocked(readFile).mockResolvedValue(
    JSON.stringify({ synthetic_only: true, access_token: token }),
  );
});
afterEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it('advertises only the enabled flag; injects the private teacher credential on the server', async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ teacher_id: 'test' }));
  vi.stubGlobal('fetch', fetcher);
  const config = await GET(
    new Request('http://localhost/api/edu/test-session'),
    context('test-session'),
  );
  expect(await config.json()).toEqual({ enabled: true });
  expect(config.headers.get('cache-control')).toBe('no-store');
  expect(readFile).not.toHaveBeenCalled();
  const result = await GET(new Request('http://localhost/api/edu/me'), context('me'));
  expect(result.status).toBe(200);
  expect(new Headers(fetcher.mock.calls[0]![1]?.headers).get('authorization')).toBe(
    `Bearer ${token}`,
  );
  expect(await result.text()).not.toContain(token);
});
it.each([
  ['disabled', 'http://localhost', 'http://127.0.0.1:8136'],
  ['browser', 'http://example.invalid', 'http://127.0.0.1:8136'],
  ['upstream', 'http://localhost', 'https://example.invalid'],
])('does not inject credentials when %s', async (reason, browser, upstream) => {
  if (reason === 'disabled') vi.stubEnv('EDU_TEST_MODE', '0');
  vi.stubEnv('EDU_API_ORIGIN', upstream);
  const fetcher = vi.fn();
  vi.stubGlobal('fetch', fetcher);
  const config = await GET(new Request(`${browser}/api/edu/test-session`), context('test-session'));
  expect(await config.json()).toEqual({ enabled: false });
  const result = await GET(new Request(`${browser}/api/edu/me`), context('me'));
  expect(result.status).toBe(401);
  expect(fetcher).not.toHaveBeenCalled();
  expect(readFile).not.toHaveBeenCalled();
});
it('rejects cross-site requests and non-whitelisted paths before reading private settings', async () => {
  vi.stubGlobal('fetch', vi.fn());
  for (const [path, headers, status] of [
    ['me', { origin: 'http://evil.invalid' }, 403],
    ['me', { 'sec-fetch-site': 'cross-site' }, 403],
    ['admin', {}, 404],
  ] as const) {
    const result = await GET(
      new Request(`http://localhost/api/edu/${path}`, { headers }),
      context(path),
    );
    expect(result.status).toBe(status);
  }
  expect(readFile).not.toHaveBeenCalled();
});
it.each(['missing', 'invalid', 'large', 'relative', 'not-synthetic'])(
  'fails visibly and redacts private file configuration: %s',
  async (fault) => {
    if (fault === 'missing')
      vi.mocked(readFile).mockRejectedValue(new Error('private-file-and-secret'));
    if (fault === 'invalid') vi.mocked(readFile).mockResolvedValue('not-json');
    if (fault === 'large') vi.mocked(stat).mockResolvedValue({ size: 4097 } as Stats);
    if (fault === 'relative') vi.stubEnv('EDU_TEST_SESSION_FILE', 'relative.json');
    if (fault === 'not-synthetic')
      vi.mocked(readFile).mockResolvedValue(JSON.stringify({ access_token: token }));
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const result = await GET(new Request('http://localhost/api/edu/me'), context('me'));
    expect(result.status).toBe(503);
    expect(await result.text()).not.toMatch(/private-file-and-secret|session.json|synthetic-local/);
    expect(fetcher).not.toHaveBeenCalled();
  },
);
it('keeps upstream revocation failures and explicit teacher credentials intact', async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ error: { code: 'UNAUTHORIZED' } }, { status: 401 }));
  vi.stubGlobal('fetch', fetcher);
  const result = await GET(new Request('http://localhost/api/edu/me'), context('me'));
  expect(result.status).toBe(401);
  vi.mocked(readFile).mockClear();
  fetcher.mockResolvedValue(Response.json({}));
  await GET(
    new Request('http://localhost/api/edu/me', {
      headers: { authorization: 'Bearer explicit-synthetic-teacher' },
    }),
    context('me'),
  );
  expect(new Headers(fetcher.mock.calls[1]![1]?.headers).get('authorization')).toBe(
    'Bearer explicit-synthetic-teacher',
  );
  expect(readFile).not.toHaveBeenCalled();
});
it('injects the same test teacher on write requests without broadening the route whitelist', async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({}));
  vi.stubGlobal('fetch', fetcher);
  const result = await POST(
    new Request('http://localhost/api/edu/batches/b/groups/query', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    }),
    context('batches/b/groups/query'),
  );
  expect(result.status).toBe(200);
  expect(new Headers(fetcher.mock.calls[0]![1]?.headers).get('authorization')).toBe(
    `Bearer ${token}`,
  );
});
