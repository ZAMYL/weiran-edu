"""Opt-in synthetic test-host API. Never imported/mounted by app.main."""

from pathlib import Path

from app.core.exact_json import ExactJSONResponse
from app.edu.query import read_query, request_schema
from app.routers.edu import Actor
from app.schemas.common import ErrorResponse
from fastapi import APIRouter, BackgroundTasks, FastAPI, Request
from starlette.concurrency import run_in_threadpool

from prototypes.teacher_agent_bridge.models import SourceRequest, SourceResult
from prototypes.teacher_agent_bridge.provider import DatabaseProvider

from .models import (
    ProfileScoreRequest,
    ProfileScoreResult,
    ReviewReceipt,
    ReviewSubmission,
    TaskCreate,
    TaskView,
)
from .review import submit
from .service import TaskService
from .store import TaskStore


def install(application: FastAPI, provider: DatabaseProvider, directory: Path) -> TaskService:
    if application.state.settings.environment != "test" or type(provider) is not DatabaseProvider:
        raise ValueError("synthetic_integration_only")
    store = TaskStore(provider)
    store.initialize()
    service = TaskService(provider, store, directory)
    router = APIRouter(
        prefix="/api/v1/edu/batches/{batch_id}/ai-investigation-tasks",
        tags=["Durable AI synthetic integration"],
        default_response_class=ExactJSONResponse,
        responses={
            code: {"model": ErrorResponse}
            for code in (401, 403, 404, 409, 410, 413, 415, 422, 499, 503)
        },
    )

    def schema(model):
        return {
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": request_schema(model)}},
            }
        }

    @router.post("", response_model=TaskView, status_code=202, openapi_extra=schema(TaskCreate))
    async def start(batch_id: str, request: Request, actor: Actor, background: BackgroundTasks):
        body = await read_query(request, TaskCreate)
        row, fresh = await run_in_threadpool(store.create, actor, batch_id, body)
        if fresh:
            background.add_task(service.execute, row["task_id"])
        return await run_in_threadpool(
            service.view, actor, batch_id, row["task_id"], body.snapshot_id
        )

    @router.get("/{task_id}", response_model=TaskView)
    def status(batch_id: str, task_id: str, snapshot_id: str, actor: Actor):
        return service.view(actor, batch_id, task_id, snapshot_id)

    @router.post("/{task_id}/cancel", response_model=TaskView)
    def cancel(batch_id: str, task_id: str, snapshot_id: str, actor: Actor):
        service.row(actor, batch_id, task_id, snapshot_id)
        store.cancel(actor, task_id)
        return service.view(actor, batch_id, task_id, snapshot_id)

    @router.post(
        "/{task_id}/records/query", response_model=SourceResult, openapi_extra=schema(SourceRequest)
    )
    async def records(batch_id: str, task_id: str, request: Request, actor: Actor):
        body = await read_query(request, SourceRequest)
        return await run_in_threadpool(service.records, actor, batch_id, task_id, body)

    @router.post(
        "/{task_id}/profiles/scores/query",
        response_model=ProfileScoreResult,
        openapi_extra=schema(ProfileScoreRequest),
    )
    async def profile_scores(batch_id: str, task_id: str, request: Request, actor: Actor):
        body = await read_query(request, ProfileScoreRequest)
        return await run_in_threadpool(service.profile_scores, actor, batch_id, task_id, body)

    @router.post(
        "/{task_id}/review-submissions",
        response_model=ReviewReceipt,
        openapi_extra=schema(ReviewSubmission),
    )
    async def review(batch_id: str, task_id: str, request: Request, actor: Actor):
        body = await read_query(request, ReviewSubmission)
        return await submit(service, actor, batch_id, task_id, body, request=request)

    application.include_router(router)
    application.state.durable_investigations = service
    return service
