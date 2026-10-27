"""Killable deterministic computation. Only trusted local child objects cross this pipe."""

import asyncio
import multiprocessing
from collections.abc import Awaitable, Callable
from multiprocessing.connection import Connection
from time import perf_counter

import anyio

from app.analysis.edu.discovery import BoundaryRequest, Policy, discover
from app.analysis.edu.discovery.models import DiscoveryResult
from app.analysis.edu.models import AnalysisInputError, Dataset
from app.core.errors import APIError

MAX_IPC_BYTES = 32 * 1024 * 1024


def compute_child(
    dataset: Dataset,
    batch_id: str,
    snapshot_id: str,
    student_ids: tuple[str, ...],
    boundaries: tuple[BoundaryRequest, ...],
    policy: Policy,
    sender: Connection,
) -> None:
    try:
        try:
            result = discover(
                dataset,
                batch_id=batch_id,
                snapshot_id=snapshot_id,
                student_ids=student_ids,
                boundaries=boundaries,
                policy=policy,
            )
            sender.send(("ok", result))
        except AnalysisInputError:
            sender.send(("data_error", None))
        except Exception:
            sender.send(("error", None))
    finally:
        sender.close()


def _receive(receiver: Connection):
    # recv_bytes has an explicit transport cap. This is our own spawned child's pickle,
    # never uploaded bytes, a request field, a cache file, or a remote model response.
    import pickle

    return pickle.loads(receiver.recv_bytes(MAX_IPC_BYTES))


def _consume_transport_error(future: asyncio.Future) -> None:
    if not future.cancelled():
        future.exception()


async def _stop(process) -> None:
    if process.pid is None:
        return
    deadline = perf_counter() + 0.5
    while process.is_alive() and perf_counter() < deadline:
        await asyncio.sleep(0.01)
    if process.is_alive():
        process.kill()
        deadline = perf_counter() + 0.5
        while process.is_alive() and perf_counter() < deadline:
            await asyncio.sleep(0.01)
    if process.is_alive():
        raise APIError(503, "DISCOVERY_UNAVAILABLE", "发现计算进程暂不可用")
    process.join(timeout=0)
    process.close()


async def execute(
    dataset: Dataset,
    batch_id: str,
    snapshot_id: str,
    student_ids: tuple[str, ...],
    boundaries: tuple[BoundaryRequest, ...],
    policy: Policy,
    *,
    timeout: float = 15,
    disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> DiscoveryResult:
    """The parent stays cancellable while a separate process owns all engine CPU work."""
    if timeout <= 0:
        raise APIError(503, "DISCOVERY_TIMEOUT", "发现计算超时，请缩小分析范围")
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=compute_child,
        args=(dataset, batch_id, snapshot_id, student_ids, boundaries, policy, sender),
        daemon=True,
    )
    future = None
    started = perf_counter()
    try:
        process.start()
        sender.close()
        future = asyncio.get_running_loop().run_in_executor(None, _receive, receiver)
        future.add_done_callback(_consume_transport_error)
        while True:
            if perf_counter() - started >= timeout:
                raise APIError(503, "DISCOVERY_TIMEOUT", "发现计算超时，请缩小分析范围")
            if disconnected is not None and await disconnected():
                # An explicit disconnect is a handled response path. Raising raw task
                # cancellation here can leave BaseHTTPMiddleware waiting for a response.
                raise APIError(499, "DISCOVERY_CANCELLED", "请求已断开，发现计算已取消")
            if future.done():
                kind, value = future.result()
                if kind == "ok" and isinstance(value, DiscoveryResult):
                    return value
                if kind == "data_error":
                    raise APIError(503, "DATA_INTEGRITY_ERROR", "发现数据校验未通过")
                raise APIError(500, "INTERNAL_ERROR", "发现计算未能完成")
            await asyncio.sleep(0.02)
    except (APIError, asyncio.CancelledError):
        raise
    except Exception:
        raise APIError(503, "DISCOVERY_UNAVAILABLE", "发现计算进程暂不可用") from None
    finally:
        # Stop the real CPU process before closing the receiver. Killing a stalled sender
        # unblocks the transport reader; a thread timeout alone would not stop computation.
        with anyio.CancelScope(shield=True):
            sender.close()
            # CPU termination must not wait for a shared thread-pool slot under load.
            # A completed result has already been received before reaching this block.
            if process.pid is not None and process.is_alive():
                process.terminate()
            await _stop(process)
            if future is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(future), timeout=1)
                except (Exception, asyncio.CancelledError):
                    pass
                if not future.done():
                    future.cancel()
            receiver.close()
