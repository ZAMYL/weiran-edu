import { ApiError } from './client';

export interface AccountLogin {
  account: string;
  password: string;
}

async function sessionRequest(method: 'POST' | 'DELETE', input?: AccountLogin) {
  let response: Response;
  try {
    response = await fetch('/api/teacher-session', {
      method,
      ...(input
        ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) }
        : {}),
      credentials: 'same-origin',
      cache: 'no-store',
      redirect: 'error',
      signal: AbortSignal.timeout(15_000),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'TimeoutError')
      throw new ApiError(504, 'REQUEST_TIMEOUT');
    throw new ApiError(0, 'NETWORK_ERROR');
  }
  if (!response.ok) {
    const result = await response.json().catch(() => null);
    const code: unknown = result?.error?.code;
    const id = response.headers.get('x-request-id');
    throw new ApiError(
      response.status,
      typeof code === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/.test(code) ? code : 'REQUEST_FAILED',
      id && /^req_[a-zA-Z0-9-]{1,100}$/.test(id) ? id : null,
    );
  }
}

export const signIn = (input: AccountLogin) => sessionRequest('POST', input);
export const signOut = () => sessionRequest('DELETE');
