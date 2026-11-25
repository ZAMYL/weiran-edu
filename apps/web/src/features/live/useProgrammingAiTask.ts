'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useDataMode } from '../../app/data-mode';
import { ApiError } from '../../services/edu-api/client';
import {
  programmingErrorGuidance,
  programmingTaskRunning,
  summarizeProgrammingResult,
  type ProgrammingStudentRef,
  type ProgrammingTask,
} from '../../services/edu-api/programming-ai-types';

function storagePointerKey(
  teacherId: string | null,
  assessmentId: string,
  sourceSnapshotId: string | null,
) {
  if (!sourceSnapshotId) return null;
  return `weiran.edu.programming-ai-task.${teacherId ?? 'anon'}.${assessmentId}.${sourceSnapshotId}`;
}

function readStoredTaskId(key: string | null): string | null {
  if (!key) return null;
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStoredTaskId(key: string | null, taskId: string | null) {
  if (!key) return;
  try {
    if (taskId) sessionStorage.setItem(key, taskId);
    else sessionStorage.removeItem(key);
  } catch {
    /* optional browser pointer */
  }
}

export function useProgrammingAiTask({
  assessmentId,
  sourceSnapshotId,
  students,
}: {
  assessmentId: string;
  sourceSnapshotId: string | null;
  students: readonly ProgrammingStudentRef[];
}) {
  const { client, teacherId } = useDataMode();
  const pointerKey = storagePointerKey(teacherId, assessmentId, sourceSnapshotId);
  const [task, setTask] = useState<ProgrammingTask | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(() => Boolean(readStoredTaskId(pointerKey)));
  const pending = useRef<AbortController | null>(null);
  const idempotencyKey = useRef<string | null>(null);

  const validateTaskSnapshot = useCallback(
    (value: ProgrammingTask) => {
      if (value.assessment_id !== assessmentId) {
        throw new ApiError(409, 'AI_SOURCE_CONFLICT');
      }
      if (sourceSnapshotId && value.source_snapshot_id !== sourceSnapshotId) {
        throw new ApiError(409, 'AI_SOURCE_CONFLICT');
      }
      return value;
    },
    [assessmentId, sourceSnapshotId],
  );

  useEffect(() => {
    const savedId = readStoredTaskId(pointerKey);
    if (!savedId) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const loaded = validateTaskSnapshot(
          await client.programmingAiTask(assessmentId, savedId, controller.signal),
        );
        if (!controller.signal.aborted) {
          setTask(loaded);
          setError(null);
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && (cause.status === 404 || cause.status === 410)) {
          writeStoredTaskId(pointerKey, null);
        } else if (cause instanceof ApiError) {
          setError(cause);
        }
      } finally {
        if (!controller.signal.aborted) setRestoring(false);
      }
    })();
    return () => {
      controller.abort();
      if (pending.current) {
        pending.current.abort();
        pending.current = null;
      }
    };
  }, [assessmentId, client, pointerKey, validateTaskSnapshot]);

  useEffect(() => {
    if (!task || !programmingTaskRunning(task) || error) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const next = validateTaskSnapshot(
          await client.programmingAiTask(assessmentId, task.task_id, controller.signal),
        );
        if (!controller.signal.aborted) {
          setTask(next);
        }
      } catch (cause) {
        if (!controller.signal.aborted && cause instanceof ApiError) {
          setError(cause);
        }
      }
    }, 600);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [assessmentId, client, error, task, validateTaskSnapshot]);

  async function operate(work: (signal: AbortSignal) => Promise<void>) {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    try {
      await work(controller.signal);
    } catch (cause) {
      if (!controller.signal.aborted && cause instanceof ApiError) {
        setError(cause);
      }
    } finally {
      if (!controller.signal.aborted) {
        pending.current = null;
        setBusy(false);
      }
    }
  }

  function startInvestigation() {
    if (!sourceSnapshotId || busy || restoring || task) return;
    if (!idempotencyKey.current) {
      idempotencyKey.current = crypto.randomUUID();
    }
    const key = idempotencyKey.current;
    void operate(async (signal) => {
      const created = validateTaskSnapshot(
        await client.startProgrammingAiTask(assessmentId, key, signal),
      );
      if (signal.aborted) return;
      setTask(created);
      writeStoredTaskId(pointerKey, created.task_id);
    });
  }

  function cancelInvestigation() {
    if (!task || !programmingTaskRunning(task) || busy) return;
    void operate(async (signal) => {
      const cancelled = validateTaskSnapshot(
        await client.cancelProgrammingAiTask(assessmentId, task.task_id, signal),
      );
      if (signal.aborted) return;
      setTask(cancelled);
    });
  }

  function reloadTask() {
    if (!task) return;
    void operate(async (signal) => {
      const reloaded = validateTaskSnapshot(
        await client.programmingAiTask(assessmentId, task.task_id, signal),
      );
      if (!signal.aborted) setTask(reloaded);
    });
  }

  const running = programmingTaskRunning(task);
  const completedResult = task?.state === 'completed' ? task.result : null;
  const summary = completedResult ? summarizeProgrammingResult(completedResult, students) : null;
  const campaignUsed = error?.status === 409 && error.code === 'AI_CAMPAIGN_ALREADY_USED';
  const errorGuidance = error
    ? programmingErrorGuidance(error.code)
    : task && !running && task.state !== 'completed'
      ? programmingErrorGuidance(
          task.error_code ??
            (task.state === 'timed_out'
              ? 'AI_TASK_TIMEOUT'
              : task.state === 'cancelled'
                ? 'AI_TASK_CANCELLED'
                : task.state === 'revoked'
                  ? 'AI_PURPOSE_REVOKED'
                  : 'AI_TASK_FAILED'),
        )
      : null;

  const canStart =
    Boolean(sourceSnapshotId) &&
    !task &&
    !restoring &&
    !campaignUsed &&
    !errorGuidance?.refreshAssessment;

  return {
    task,
    error,
    busy,
    restoring,
    running,
    completedResult,
    summary,
    errorGuidance,
    campaignUsed,
    canStart,
    startInvestigation,
    cancelInvestigation,
    reloadTask,
  };
}
