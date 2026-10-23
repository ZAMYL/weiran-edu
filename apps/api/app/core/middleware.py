from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response

from app.core.errors import handle_internal_error


async def attach_request_id(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request.state.request_id = f"req_{uuid4().hex}"
    try:
        response = await call_next(request)
    except Exception as exc:
        # Sanitize at the HTTP boundary before the server can log private exception details.
        response = await handle_internal_error(request, exc)
    response.headers["X-Request-ID"] = request.state.request_id
    return response
