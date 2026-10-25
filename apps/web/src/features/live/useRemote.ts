'use client';
import { useEffect, useState } from 'react';
import { ApiError } from '../../services/edu-api/client';

export function useRemote<T>(identity: string | null, load: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const key = identity === null ? null : `${identity}:${attempt}`;
  const [result, setResult] = useState<{
    key: string | null;
    data: T | null;
    error: ApiError | null;
  }>({ key: null, data: null, error: null });
  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setResult({ key, data, error: null });
      })
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          !(error instanceof DOMException && error.name === 'AbortError')
        )
          setResult({
            key,
            data: null,
            error: error instanceof ApiError ? error : new ApiError(502, 'INVALID_API_RESPONSE'),
          });
      });
    return () => controller.abort();
  }, [key, load]);
  const active = key !== null && result.key === key;
  return {
    data: active ? result.data : null,
    error: active ? result.error : null,
    loading: key !== null && !active,
    retry: () => setAttempt((n) => n + 1),
  };
}
