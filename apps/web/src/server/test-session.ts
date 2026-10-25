// Server-side local integration adapter. Never import this from a client component.
import { readFile, stat } from 'node:fs/promises';
import { isAbsolute } from 'node:path';

const loopback = (hostname: string) => ['localhost', '127.0.0.1', '[::1]'].includes(hostname);

export function testSessionEnabled(request: Request, upstream: URL): boolean {
  if (process.env.EDU_TEST_MODE !== '1') return false;
  try {
    const source = new URL(request.url);
    const host = request.headers.get('host');
    const browser = host ? new URL(`${source.protocol}//${host}`) : source;
    return loopback(browser.hostname) && loopback(upstream.hostname);
  } catch {
    return false;
  }
}

export async function testSessionAuthorization(): Promise<string> {
  const path = process.env.EDU_TEST_SESSION_FILE;
  if (!path || !isAbsolute(path) || (await stat(path)).size > 4096)
    throw new Error('TEST_SESSION_NOT_CONFIGURED');
  const value: unknown = JSON.parse(await readFile(path, 'utf8'));
  if (
    !value ||
    typeof value !== 'object' ||
    !('synthetic_only' in value) ||
    value.synthetic_only !== true ||
    !('access_token' in value) ||
    typeof value.access_token !== 'string' ||
    !/^[a-zA-Z0-9_-]{20,200}$/.test(value.access_token)
  )
    throw new Error('TEST_SESSION_NOT_CONFIGURED');
  return `Bearer ${value.access_token}`;
}
