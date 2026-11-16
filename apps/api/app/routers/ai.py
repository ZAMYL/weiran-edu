"""Fail-closed AI task surface for the formal app.

The executable synthetic host installs its own routes and sets
``app.state.ai_task_service``. Until a server-owned Live service is installed,
these routes exist so clients receive an explicit "not enabled" response instead
of a misleading 404.
"""

from typing import Literal

from fastapi import APIRouter, Request

from app.core.errors import APIError
from app.core.exact_json import ExactJSONResponse
from app.edu.models import Model
from app.schemas.common import ErrorResponse

from .edu import Actor

AI_UNAVAILABLE = "AI 分析暂不可用"

capability_router = APIRouter(
    prefix="/api/v1/edu", tags=["AI"], default_response_class=ExactJSONResponse
)
task_router = APIRouter(
    prefix="/api/v1/edu/batches/{batch_id}/ai-investigation-tasks",
    tags=["AI"],
    default_response_class=ExactJSONResponse,
    responses={503: {"model": ErrorResponse}},
)


class AICapability(Model):
    available: bool
    mode: Literal["qwen_live", "http_mock", "unavailable"]
    reason: Literal["available", "AI_LIVE_NOT_ENABLED"]
    message: str


def _service(request: Request):
    return getattr(request.app.state, "ai_task_service", None)


def _unavailable() -> None:
    raise APIError(503, "AI_LIVE_NOT_ENABLED", AI_UNAVAILABLE)


@capability_router.get("/ai-capability", response_model=AICapability)
def ai_capability(request: Request, actor: Actor) -> AICapability:
    service = _service(request)
    if service is None:
        return AICapability(
            available=False,
            mode="unavailable",
            reason="AI_LIVE_NOT_ENABLED",
            message=AI_UNAVAILABLE,
        )
    mode = getattr(request.app.state, "ai_task_mode", "http_mock")
    return AICapability(
        available=True,
        mode=mode,
        reason="available",
        message="合成演示 AI 可用" if mode == "http_mock" else "AI 分析可用",
    )


@task_router.post("", status_code=202)
def start_task(batch_id: str, request: Request, actor: Actor) -> None:
    _unavailable()


@task_router.get("/{task_id}")
def task_status(
    batch_id: str,
    task_id: str,
    request: Request,
    actor: Actor,
    snapshot_id: str | None = None,
) -> None:
    _unavailable()


@task_router.post("/{task_id}/cancel")
def cancel_task(
    batch_id: str,
    task_id: str,
    request: Request,
    actor: Actor,
    snapshot_id: str | None = None,
) -> None:
    _unavailable()


@task_router.post("/{task_id}/records/query")
def task_records(batch_id: str, task_id: str, request: Request, actor: Actor) -> None:
    _unavailable()


@task_router.post("/{task_id}/profiles/scores/query")
def task_profile_scores(batch_id: str, task_id: str, request: Request, actor: Actor) -> None:
    _unavailable()


@task_router.post("/{task_id}/review-submissions")
def task_review(batch_id: str, task_id: str, request: Request, actor: Actor) -> None:
    _unavailable()


@task_router.get("/{task_id}/events")
def task_events(
    batch_id: str,
    task_id: str,
    request: Request,
    actor: Actor,
    snapshot_id: str | None = None,
    after: int = 0,
) -> None:
    _unavailable()
