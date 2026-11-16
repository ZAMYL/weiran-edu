from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.core.config import Settings
from app.core.errors import (
    APIError,
    handle_api_error,
    handle_http_error,
    handle_internal_error,
    handle_validation_error,
)
from app.core.middleware import attach_request_id
from app.routers.ai import capability_router as ai_capability_router
from app.routers.ai import task_router as ai_task_router
from app.routers.health import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="未然 EDU API", version="0.1.0", debug=False)
    application.state.settings = settings if settings is not None else Settings.from_env()
    application.middleware("http")(attach_request_id)
    application.add_exception_handler(APIError, handle_api_error)
    application.add_exception_handler(HTTPException, handle_http_error)
    application.add_exception_handler(RequestValidationError, handle_validation_error)
    application.add_exception_handler(Exception, handle_internal_error)
    application.include_router(health_router)
    if application.state.settings.database_path is not None:
        from app.edu.discovery_disconnect import DiscoveryDisconnectMiddleware
        from app.edu.service import EduService
        from app.importing.edu.worker import parse_isolated
        from app.routers.edu import router as edu_router
        from app.routers.edu_discoveries import router as edu_discoveries_router
        from app.routers.edu_reviews import router as edu_reviews_router
        from app.routers.edu_support import router as edu_support_router
        from app.storage.database import Database

        application.state.edu_service = EduService(
            Database(application.state.settings.database_path),
            parser=parse_isolated,
        )
        application.include_router(edu_router)
        application.include_router(edu_discoveries_router)
        application.include_router(edu_reviews_router)
        application.include_router(edu_support_router)
        application.include_router(ai_capability_router)
        if application.state.settings.ai_task_routes:
            application.include_router(ai_task_router)
        application.middleware("http")(protect_edu_responses)
        application.add_middleware(DiscoveryDisconnectMiddleware)
    return application


async def protect_edu_responses(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    response = await call_next(request)
    if request.url.path.startswith("/api/v1/edu"):
        response.headers["Cache-Control"] = "no-store"
    return response
