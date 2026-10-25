import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.schemas.common import ErrorDetails, ErrorResponse

logger = logging.getLogger(__name__)


class APIError(Exception):
    """An intentional failure carrying only a developer-defined public message."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


HTTP_ERRORS = {
    400: ("BAD_REQUEST", "请求格式不正确"),
    401: ("UNAUTHORIZED", "请求未获授权"),
    403: ("FORBIDDEN", "无权访问该资源"),
    404: ("RESOURCE_NOT_FOUND", "资源不存在"),
    405: ("METHOD_NOT_ALLOWED", "该接口不支持此请求方法"),
    422: ("VALIDATION_ERROR", "请求参数不符合要求"),
    429: ("RATE_LIMITED", "请求过于频繁，请稍后重试"),
    503: ("SERVICE_UNAVAILABLE", "服务暂时不可用"),
}


def error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request.state.diagnostic_error_code = code
    request_id = request.state.request_id
    payload = ErrorResponse(error=ErrorDetails(code=code, message=message, request_id=request_id))
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
    )


async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
    response = error_response(request, exc.status_code, exc.code, exc.message)
    if exc.status_code == 401:
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code, message = HTTP_ERRORS.get(exc.status_code, ("HTTP_ERROR", "请求暂时无法处理"))
    response = error_response(request, exc.status_code, code, message)
    for name, value in (exc.headers or {}).items():
        if name.lower() in {"allow", "www-authenticate", "retry-after"}:
            response.headers[name] = value
    return response


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Validation errors can include submitted values and arbitrary field names.
    return error_response(request, 422, "VALIDATION_ERROR", "请求参数不符合要求")


async def handle_internal_error(request: Request, exc: Exception) -> JSONResponse:
    # Keep exception messages, traces, URLs and request bodies out of application logs.
    logger.error("Request failed: request_id=%s", request.state.request_id)
    return error_response(request, 500, "INTERNAL_ERROR", "服务内部错误，请稍后重试")
