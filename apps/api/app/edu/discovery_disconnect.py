"""Observe discovery disconnects outside BaseHTTPMiddleware without buffering request data."""

import asyncio
from collections.abc import Callable
from threading import Event

import anyio
from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import APIError

type CancelCheck = Callable[[], bool]


def cancellation(request: Request | None) -> CancelCheck | None:
    event = getattr(request.state, "edu_discovery_cancelled", None) if request else None
    return event.is_set if event is not None else None


def check_cancelled(cancelled: CancelCheck | None) -> None:
    if cancelled is not None and cancelled():
        raise APIError(499, "DISCOVERY_CANCELLED", "请求已断开，计算或导出已取消")


class DiscoveryDisconnectMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path.startswith("/api/v1/edu/batches/")
            and (
                "/discoveries/" in path
                or (scope.get("method") == "POST" and "/ai-investigations" in path)
                or (
                    scope.get("method") == "POST"
                    and "/ai-investigation-tasks/" in path
                    and path.endswith("/review-submissions")
                )
                or (scope.get("method") == "POST" and path.endswith("/analysis-results"))
                or (scope.get("method") == "GET" and path.endswith("/export"))
                or (scope.get("method") == "POST" and path.endswith("/export-check"))
            )
        ):
            await self.app(scope, receive, send)
            return
        disconnected = asyncio.Event()
        scope.setdefault("state", {})["edu_discovery_disconnect"] = disconnected
        cancelled = Event()  # Read safely from synchronous export/validation worker threads.
        scope["state"]["edu_discovery_cancelled"] = cancelled
        owner = asyncio.current_task()
        body_complete = False
        async with anyio.create_task_group() as tasks:

            async def watch_task_cancellation() -> None:
                # BaseHTTPMiddleware may shield a synchronous endpoint while its parent
                # task is cancelling. Signal that endpoint before waiting for cleanup.
                with anyio.CancelScope(shield=True):
                    while not cancelled.is_set():
                        if owner is not None and owner.cancelling():
                            cancelled.set()
                            return
                        await anyio.sleep(0.02)

            tasks.start_soon(watch_task_cancellation)

            async def watch() -> None:
                try:
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            disconnected.set()
                            cancelled.set()
                            return
                except Exception:
                    disconnected.set()
                    cancelled.set()
                finally:
                    # Task/server cancellation must also stop synchronous export workers;
                    # their shielded thread cleanup cannot wait for our outer finally.
                    cancelled.set()

            async def observed_receive() -> Message:
                nonlocal body_complete
                if body_complete:
                    await disconnected.wait()
                    return {"type": "http.disconnect"}
                # Never pre-read or buffer the body: the authenticated bounded reader
                # remains its sole consumer. Only listen after it consumed the last chunk.
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected.set()
                    cancelled.set()
                elif message["type"] == "http.request" and not message.get("more_body", False):
                    body_complete = True
                    tasks.start_soon(watch)
                return message

            try:
                # GET downloads have no body reader to start the observer. Their query
                # parameters are the complete input; never buffer any incoming bytes.
                if scope.get("method") == "GET":
                    body_complete = True
                    tasks.start_soon(watch)
                await self.app(scope, observed_receive, send)
            finally:
                cancelled.set()
                tasks.cancel_scope.cancel()
