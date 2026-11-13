import { createHash, timingSafeEqual } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import { isAbsolute } from 'node:path';

const cookie = 'weiran_teacher_session';
const testCookie = 'weiran_teacher_test_session';
export const tokenPattern = /^[a-zA-Z0-9_-]{20,200}$/;

export function browserOrigin(request: Request) {
  const url = new URL(request.url);
  const host = request.headers.get('host');
  if (host && /^[a-zA-Z0-9.\[\]:-]+$/.test(host)) {
    try {
      return new URL(`${url.protocol}//${host}`).origin;
    } catch {
      // Use the parsed request URL when the Host header is invalid.
    }
  }
  return url.origin;
}

export function teacherApiOrigin() {
  try {
    const url = new URL(process.env.EDU_API_ORIGIN ?? 'http://127.0.0.1:8000');
    if (
      /^https?:$/.test(url.protocol) &&
      !url.username &&
      !url.password &&
      url.pathname === '/' &&
      !url.search &&
      !url.hash
    )
      return url;
  } catch {
    // Invalid deployment configuration.
  }
  return null;
}

const loopback = (url: URL) => ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
export const secureTeacherUpstream = (url: URL) => url.protocol === 'https:' || loopback(url);
export function isAccountTest(request: Request, upstream: URL) {
  return (
    process.env.EDU_ACCOUNT_TEST_MODE === '1' &&
    loopback(new URL(browserOrigin(request))) &&
    loopback(upstream)
  );
}

export function readTeacherSession(request: Request, upstream: URL) {
  const test = isAccountTest(request, upstream);
  if (
    !test &&
    (new URL(browserOrigin(request)).protocol !== 'https:' || !secureTeacherUpstream(upstream))
  )
    return null;
  const name = test ? testCookie : cookie;
  const values = (request.headers.get('cookie') ?? '')
    .split(';')
    .map((part) => part.trim())
    .filter((part) => part.startsWith(name + '='));
  const token = values.length === 1 ? values[0]!.slice(name.length + 1) : '';
  return tokenPattern.test(token) ? { token, test } : null;
}

export function sessionCookie(request: Request, test: boolean, token: string, age: number) {
  const secure = new URL(browserOrigin(request)).protocol === 'https:' ? '; Secure' : '';
  return `${test ? testCookie : cookie}=${token}; Path=/api; HttpOnly; SameSite=Strict; Max-Age=${age}${secure}`;
}

export async function accountTestToken(account: string, password: string) {
  const path = process.env.EDU_ACCOUNT_TEST_FILE;
  if (!path || !isAbsolute(path) || (await stat(path)).size > 4096)
    throw new Error('TEST_ACCOUNT_CONFIG');
  const data: unknown = JSON.parse(await readFile(path, 'utf8'));
  if (
    !data ||
    typeof data !== 'object' ||
    !('synthetic_only' in data) ||
    data.synthetic_only !== true ||
    !('account' in data) ||
    typeof data.account !== 'string' ||
    !('password' in data) ||
    typeof data.password !== 'string' ||
    !('access_token' in data) ||
    typeof data.access_token !== 'string' ||
    !tokenPattern.test(data.access_token)
  )
    throw new Error('TEST_ACCOUNT_CONFIG');

  const hash = (value: string) => createHash('sha256').update(value).digest();
  const matchesAccount = timingSafeEqual(hash(account), hash(data.account));
  const matchesPassword = timingSafeEqual(hash(password), hash(data.password));
  return matchesAccount && matchesPassword ? data.access_token : null;
}
