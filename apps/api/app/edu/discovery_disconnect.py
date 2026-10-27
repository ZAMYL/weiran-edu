"""Observe discovery disconnects outside BaseHTTPMiddleware without buffering request data."""

import asyncio

import anyio
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class DiscoveryDisconnectMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path.startswith("/api/v1/edu/batches/") and "/discoveries/" in path
        ):
            await self.app(scope, receive, send)
            return
        disconnected = asyncio.Event()
        scope.setdefault("state", {})["edu_discovery_disconnect"] = disconnected
        body_complete = False
        async with anyio.create_task_group() as tasks:

            async def watch() -> None:
                try:
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            disconnected.set()
                            return
                except Exception:
                    disconnected.set()

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
                elif message["type"] == "http.request" and not message.get("more_body", False):
                    body_complete = True
                    tasks.start_soon(watch)
                return message

            try:
                await self.app(scope, observed_receive, send)
            finally:
                tasks.cancel_scope.cancel()
