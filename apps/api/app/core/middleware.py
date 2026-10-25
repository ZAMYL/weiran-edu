from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from fastapi import Request, Response

from app.core.errors import handle_internal_error
from app.core.request_tracing import capture_trace


async def attach_request_id(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request.state.request_id = f"req_{uuid4().hex}"
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        # Sanitize at the HTTP boundary before the server can log private exception details.
        response = await handle_internal_error(request, exc)
    response.headers["X-Request-ID"] = request.state.request_id
    try:
        await capture_trace(request, response, started)
    except Exception:
        # Telemetry must never mask a successful commit or the original business error.
        if getattr(request.state, "trace_teacher_id", None) is not None:
            response.headers["X-Request-Trace"] = "unavailable"
    return response
