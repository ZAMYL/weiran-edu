import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DELETE, POST } from './route';
import { GET, POST as relayPost } from '../edu/[...path]/route';

const token = 'synthetic-account-test-session-token';
const input = { account: 'test-teacher', password: 'synthetic-test-password' };
const origin = 'https://edu.example.test';
const upstream = 'https://api.example.test';
const teacherCookie = `weiran_teacher_session=${token}`;
const request = (method: 'POST' | 'DELETE', headers: HeadersInit = { origin }) =>
  new Request(origin + '/api/teacher-session', {
    method,
    headers: { 'content-type': 'application/json', ...headers },
    ...(method === 'POST' ? { body: JSON.stringify(input) } : {}),
  });
const route = (path: string) => ({ params: Promise.resolve({ path: path.split('/') }) });
const transport = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal('fetch', transport);
  vi.stubEnv('EDU_API_ORIGIN', upstream);
  vi.stubEnv('EDU_ACCOUNT_LOGIN_ENABLED', '1');
  vi.stubEnv('EDU_ACCOUNT_TEST_MODE', '0');
  vi.stubEnv('EDU_TEST_MODE', '0');
});

afterEach(() => {
  transport.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it('sets a secure cookie only after validating the backend credential and returns no secret', async () => {
  transport.mockResolvedValueOnce(Response.json({ access_token: token, expires_in: 120 }));
  transport.mockResolvedValueOnce(Response.json({ teacher_id: 'teacher-1' }));
  const response = await POST(request('POST'));
  expect(response.status).toBe(200);
  expect(response.headers.get('set-cookie')).toBe(
    `${teacherCookie}; Path=/api; HttpOnly; SameSite=Strict; Max-Age=120; Secure`,
  );
  expect(await response.json()).toEqual({ teacher_id: 'teacher-1' });
  expect(String(transport.mock.calls[0]![0])).toBe(upstream + '/api/v1/edu/auth/login');
  expect(JSON.parse(String(transport.mock.calls[0]![1]?.body))).toEqual(input);
  expect(new Headers(transport.mock.calls[1]![1]?.headers).get('authorization')).toBe(
    `Bearer ${token}`,
  );
});

it('blocks cross-origin login and logout, insecure transport and disabled production login', async () => {
  const crossOrigin = { origin: 'https://other.example.test' };
  expect((await POST(request('POST', crossOrigin))).status).toBe(403);
  expect((await DELETE(request('DELETE', crossOrigin))).status).toBe(403);
  const insecure = 'http://edu.example.test';
  expect(
    (
      await POST(
        new Request(insecure + '/api/teacher-session', {
          method: 'POST',
          headers: { origin: insecure },
        }),
      )
    ).status,
  ).toBe(503);
  vi.stubEnv('EDU_API_ORIGIN', 'http://api.example.test');
  expect((await POST(request('POST'))).status).toBe(503);
  vi.stubEnv('EDU_API_ORIGIN', upstream);
  vi.stubEnv('EDU_ACCOUNT_LOGIN_ENABLED', '0');
  expect((await POST(request('POST'))).status).toBe(503);
  expect(transport).not.toHaveBeenCalled();
});

it('preserves invalid login status without exposing the upstream response', async () => {
  transport.mockResolvedValue(Response.json({ secret: input.password }, { status: 401 }));
  const response = await POST(request('POST'));
  expect(response.status).toBe(401);
  expect(response.headers.has('set-cookie')).toBe(false);
  expect(await response.json()).toEqual({ error: { code: 'INVALID_LOGIN' } });
});

it('rejects an invalid token response instead of creating a session', async () => {
  transport.mockResolvedValue(Response.json({ access_token: token, expires_in: 0 }));
  const response = await POST(request('POST'));
  expect(response.status).toBe(502);
  expect(response.headers.has('set-cookie')).toBe(false);
  expect(transport).toHaveBeenCalledTimes(1);
});

it('revokes the current session on logout and expires both cookie names', async () => {
  transport.mockResolvedValue(new Response(null, { status: 204 }));
  const response = await DELETE(request('DELETE', { origin, cookie: teacherCookie }));
  expect(response.status).toBe(204);
  expect(response.headers.getSetCookie()).toHaveLength(2);
  expect(response.headers.getSetCookie().every((value) => value.includes('Max-Age=0'))).toBe(true);
  expect(String(transport.mock.calls[0]![0])).toBe(upstream + '/api/v1/edu/auth/logout');
  expect(new Headers(transport.mock.calls[0]![1]?.headers).get('authorization')).toBe(
    `Bearer ${token}`,
  );
});

it('relays only the validated teacher cookie and clears it when the backend revokes access', async () => {
  transport.mockResolvedValue(Response.json({ error: { code: 'UNAUTHORIZED' } }, { status: 401 }));
  const response = await GET(
    new Request(origin + '/api/edu/me', {
      headers: { cookie: teacherCookie + '; unrelated=private' },
    }),
    route('me'),
  );
  expect(response.status).toBe(401);
  expect(response.headers.get('set-cookie')).toContain('Max-Age=0');
  const headers = new Headers(transport.mock.calls[0]![1]?.headers);
  expect(headers.get('authorization')).toBe(`Bearer ${token}`);
  expect(headers.has('cookie')).toBe(false);
});

it('requires a matching Origin on cookie writes and ignores test cookies outside local test mode', async () => {
  const groups = 'batches/sample/groups/query';
  const denied = await relayPost(
    new Request(origin + '/api/edu/' + groups, {
      method: 'POST',
      headers: { cookie: teacherCookie },
    }),
    route(groups),
  );
  expect(denied.status).toBe(403);
  const test = await GET(
    new Request(origin + '/api/edu/me', {
      headers: { cookie: `weiran_teacher_test_session=${token}` },
    }),
    route('me'),
  );
  expect(test.status).toBe(401);
  expect(transport).not.toHaveBeenCalled();
});
