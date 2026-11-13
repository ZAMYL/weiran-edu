import {
  accountTestToken,
  browserOrigin,
  isAccountTest,
  readTeacherSession,
  secureTeacherUpstream,
  sessionCookie,
  teacherApiOrigin,
  tokenPattern,
} from '../../../server/teacher-session';
import { meDto } from '../../../services/edu-api/types';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
const noStore = { 'Cache-Control': 'no-store' };
const lifetime = 3600;
const limit = 4096;

function fail(status: number, code: string, requestId?: string | null) {
  const id = requestId && /^req_[a-zA-Z0-9-]{1,100}$/.test(requestId) ? requestId : null;
  return Response.json(
    { error: { code, ...(id ? { request_id: id } : {}) } },
    {
      status,
      headers: { ...noStore, ...(id ? { 'X-Request-ID': id } : {}) },
    },
  );
}

function isSameOrigin(request: Request) {
  return (
    request.headers.get('origin') === browserOrigin(request) &&
    request.headers.get('sec-fetch-site') !== 'cross-site' &&
    !new URL(request.url).search
  );
}

async function readLogin(request: Request) {
  if (
    request.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase() !== 'application/json'
  )
    return fail(415, 'INVALID_LOGIN_FORMAT');
  if (Number(request.headers.get('content-length')) > limit) return fail(413, 'REQUEST_TOO_LARGE');
  const reader = request.body?.getReader();
  if (!reader) return fail(422, 'INVALID_LOGIN_INPUT');
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let body = '';
  let size = 0;
  try {
    for (;;) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.length;
      if (size > limit) {
        await reader.cancel();
        return fail(413, 'REQUEST_TOO_LARGE');
      }
      body += decoder.decode(part.value, { stream: true });
    }
    const value: unknown = JSON.parse(body + decoder.decode());
    if (
      value &&
      typeof value === 'object' &&
      'account' in value &&
      'password' in value &&
      typeof value.account === 'string' &&
      value.account.trim().length > 0 &&
      value.account.trim().length <= 128 &&
      typeof value.password === 'string' &&
      value.password.length > 0 &&
      value.password.length <= 256
    )
      return { account: value.account.trim(), password: value.password };
  } catch {
    // Do not echo input or parser errors.
  } finally {
    reader.releaseLock();
  }
  return fail(422, 'INVALID_LOGIN_INPUT');
}

export async function POST(request: Request) {
  if (!isSameOrigin(request)) return fail(403, 'ORIGIN_NOT_ALLOWED');
  const upstream = teacherApiOrigin();
  if (!upstream) return fail(503, 'BACKEND_NOT_CONFIGURED');
  const test = isAccountTest(request, upstream);
  if (
    !test &&
    (process.env.EDU_ACCOUNT_LOGIN_ENABLED !== '1' ||
      new URL(browserOrigin(request)).protocol !== 'https:' ||
      !secureTeacherUpstream(upstream))
  )
    return fail(503, 'ACCOUNT_LOGIN_UNAVAILABLE');
  const input = await readLogin(request);
  if (input instanceof Response) return input;
  const timeout = AbortSignal.timeout(10_000);
  const options = {
    cache: 'no-store',
    redirect: 'error',
    signal: AbortSignal.any([timeout, request.signal]),
  } as const;
  try {
    let token: string;
    let age = lifetime;
    if (test) {
      const candidate = await accountTestToken(input.account, input.password);
      if (!candidate) return fail(401, 'INVALID_LOGIN');
      token = candidate;
    } else {
      const response = await fetch(new URL('/api/v1/edu/auth/login', upstream), {
        ...options,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      });
      if (!response.ok)
        return fail(
          response.status,
          response.status === 401 ? 'INVALID_LOGIN' : 'ACCOUNT_LOGIN_FAILED',
          response.headers.get('x-request-id'),
        );
      const result: unknown = await response.json().catch(() => null);
      if (
        !result ||
        typeof result !== 'object' ||
        !('access_token' in result) ||
        typeof result.access_token !== 'string' ||
        !tokenPattern.test(result.access_token) ||
        !('expires_in' in result) ||
        typeof result.expires_in !== 'number' ||
        !Number.isSafeInteger(result.expires_in) ||
        result.expires_in < 1
      )
        return fail(502, 'INVALID_API_RESPONSE');
      token = result.access_token;
      age = Math.min(lifetime, result.expires_in);
    }
    const response = await fetch(new URL('/api/v1/edu/me', upstream), {
      ...options,
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok)
      return fail(
        response.status,
        response.status === 401 ? 'INVALID_LOGIN' : 'ACCOUNT_LOGIN_FAILED',
        response.headers.get('x-request-id'),
      );
    let identity;
    try {
      identity = meDto(await response.json());
    } catch {
      return fail(502, 'INVALID_API_RESPONSE');
    }
    return Response.json(identity, {
      headers: { ...noStore, 'Set-Cookie': sessionCookie(request, test, token, age) },
    });
  } catch {
    return timeout.aborted ? fail(504, 'REQUEST_TIMEOUT') : fail(503, 'ACCOUNT_LOGIN_UNAVAILABLE');
  }
}

export async function DELETE(request: Request) {
  if (!isSameOrigin(request)) return fail(403, 'ORIGIN_NOT_ALLOWED');
  const upstream = teacherApiOrigin();
  const session = upstream && readTeacherSession(request, upstream);
  let status = 204;
  if (session && !session.test) {
    try {
      const response = await fetch(new URL('/api/v1/edu/auth/logout', upstream), {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.token}` },
        cache: 'no-store',
        redirect: 'error',
        signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok && response.status !== 401) status = 503;
    } catch {
      status = 503;
    }
  }
  if (status !== 204) return fail(status, 'LOGOUT_NOT_CONFIRMED');
  const response = new Response(null, { status, headers: noStore });
  response.headers.append('Set-Cookie', sessionCookie(request, false, '', 0));
  response.headers.append('Set-Cookie', sessionCookie(request, true, '', 0));
  return response;
}
