"""Only server route templates and safe outcome metadata may enter telemetry."""

import logging
import re
from time import perf_counter

from fastapi import Request, Response
from starlette.concurrency import run_in_threadpool

from app.storage.request_traces import RequestTraceStore

logger = logging.getLogger(__name__)


def stage_for(status: int) -> str:
    if status < 400:
        return "completed"
    return {
        401: "authentication",
        403: "authorization_or_resource",
        404: "authorization_or_resource",
        409: "conflict",
        413: "limit",
        415: "validation",
        422: "validation",
        429: "limit",
        503: "service",
    }.get(status, "server" if status >= 500 else "request")


async def capture_trace(request: Request, response: Response, started: float) -> None:
    teacher_id = getattr(request.state, "trace_teacher_id", None)
    if teacher_id is None:
        return
    route = getattr(request.scope.get("route"), "path_format", None)
    # Do not fall back to the submitted URI. Only allow an actual registered template.
    if not isinstance(route, str) or route not in request.app.openapi()["paths"]:
        return
    if route == "/api/v1/edu/diagnostics/requests/{request_id}":
        return  # No recursive logging of lookups.
    path = request.app.state.settings.request_trace_database_path
    if path is None:
        response.headers["X-Request-Trace"] = "disabled"
        return
    code = getattr(request.state, "diagnostic_error_code", None)
    if code is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
        code = "REQUEST_FAILED"
    try:
        store = RequestTraceStore(path)
        recorded = await run_in_threadpool(
            store.record,
            request_id=request.state.request_id,
            teacher_id=teacher_id,
            route_template=route,
            method=request.method,
            status_code=response.status_code,
            error_code=code,
            stage=stage_for(response.status_code),
            duration_ms=int((perf_counter() - started) * 1000),
        )
    except Exception:
        recorded = False
    response.headers["X-Request-Trace"] = "recorded" if recorded else "unavailable"
    if not recorded:
        logger.warning("Request trace unavailable: request_id=%s", request.state.request_id)
