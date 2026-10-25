import logging
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.core.errors import APIError
from app.schemas.common import ErrorResponse, HealthResponse
from app.storage.readiness import readiness_failure

router = APIRouter(prefix="/api/v1", tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    return HealthResponse(status="ok")


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"


@router.get(
    "/health/ready", response_model=ReadinessResponse, responses={503: {"model": ErrorResponse}}
)
def get_readiness(request: Request, response: Response) -> ReadinessResponse:
    failure = readiness_failure(request.app.state.settings.database_path)
    if failure is not None:
        logger.warning("Readiness failed: code=%s request_id=%s", failure, request.state.request_id)
        raise APIError(503, "SERVICE_NOT_READY", "服务尚未就绪")
    response.headers["Cache-Control"] = "no-store"
    return ReadinessResponse()
