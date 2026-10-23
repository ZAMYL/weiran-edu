from fastapi import FastAPI
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
    return application
