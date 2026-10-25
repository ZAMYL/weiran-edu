'use client';
import { useEffect, useMemo, useState } from 'react';
import { ApiError } from '../../services/edu-api/client';

export function useRemote<T>(identity: string | null, load: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const request = useMemo(
    () => (identity === null ? null : { identity, attempt, load }),
    [identity, attempt, load],
  );
  const [result, setResult] = useState<{
    request: typeof request;
    data: T | null;
    error: ApiError | null;
  }>({ request: null, data: null, error: null });
  useEffect(() => {
    if (request === null) return;
    const controller = new AbortController();
    request
      .load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setResult({ request, data, error: null });
      })
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          !(error instanceof DOMException && error.name === 'AbortError')
        )
          setResult({
            request,
            data: null,
            error: error instanceof ApiError ? error : new ApiError(502, 'INVALID_API_RESPONSE'),
          });
      });
    return () => controller.abort();
  }, [request]);
  const active = request !== null && result.request === request;
  return {
    data: active ? result.data : null,
    error: active ? result.error : null,
    loading: request !== null && !active,
    retry: () => setAttempt((n) => n + 1),
  };
}
