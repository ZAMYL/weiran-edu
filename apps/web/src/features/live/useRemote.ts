'use client';
import { useEffect, useMemo, useState } from 'react';
import { ApiError } from '../../services/edu-api/client';

export interface OverviewCache {
  read: <T>(key: string) => T | null;
  write: <T>(key: string, data: T) => void;
  evict: (key: string) => void;
}

export function useRemote<T>(
  identity: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  cache?: OverviewCache,
  defer = false,
) {
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
    if (request === null || defer) return;
    const controller = new AbortController();
    request
      .load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          cache?.write(request.identity, data);
          setResult({ request, data, error: null });
        }
      })
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          !(error instanceof DOMException && error.name === 'AbortError')
        ) {
          cache?.evict(request.identity);
          setResult({
            request,
            data: null,
            error: error instanceof ApiError ? error : new ApiError(502, 'INVALID_API_RESPONSE'),
          });
        }
      });
    return () => controller.abort();
  }, [request, cache, defer]);
  const active = request !== null && result.request === request;
  const data = active ? result.data : request ? (cache?.read<T>(request.identity) ?? null) : null;
  return {
    data,
    error: active ? result.error : null,
    loading: request !== null && !active && data === null,
    refreshing: request !== null && !active && data !== null,
    retry: () => {
      if (identity) cache?.evict(identity);
      setAttempt((n) => n + 1);
    },
  };
}
