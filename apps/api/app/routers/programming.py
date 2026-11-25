"""编程测评调查任务的正式入口：只暴露测评作用域，浏览器不参与来源与预算绑定。"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response

from app.core.errors import APIError
from app.edu.programming_tasks import (
    ProgrammerTaskCreate,
    ProgrammingTaskService,
    ProgrammingTaskView,
)
from app.routers.edu import Actor


def programming_tasks(request: Request) -> ProgrammingTaskService:
    service = getattr(request.app.state, "programming_tasks", None)
    if service is None:
        raise APIError(503, "AI_LIVE_NOT_ENABLED", "编程调查任务未启用")
    return service


type Tasks = Annotated[ProgrammingTaskService, Depends(programming_tasks)]

router = APIRouter(prefix="/api/v1/edu/assessments/{assessment_id}/ai-tasks", tags=["edu"])


@router.post("", response_model=ProgrammingTaskView, status_code=202)
def create_task(
    assessment_id: str,
    body: ProgrammerTaskCreate,
    response: Response,
    actor: Actor,
    tasks: Tasks,
    background: BackgroundTasks,
) -> ProgrammingTaskView:
    row, fresh = tasks.create(actor, assessment_id, body)
    view = tasks.view(actor, row["task_id"])
    if not fresh:
        response.status_code = 200
        return view
    background.add_task(tasks.execute, row["task_id"])
    return view


@router.get("/{task_id}", response_model=ProgrammingTaskView)
def get_task(assessment_id: str, task_id: str, actor: Actor, tasks: Tasks) -> ProgrammingTaskView:
    view = tasks.view(actor, task_id)
    if view.assessment_id != assessment_id:
        raise APIError(404, "RESOURCE_NOT_FOUND", "任务不属于该测评")
    return view


@router.post("/{task_id}/cancel", response_model=ProgrammingTaskView)
def cancel_task(
    assessment_id: str, task_id: str, actor: Actor, tasks: Tasks
) -> ProgrammingTaskView:
    view = tasks.view(actor, task_id)
    if view.assessment_id != assessment_id:
        raise APIError(404, "RESOURCE_NOT_FOUND", "任务不属于该测评")
    return tasks.cancel(actor, task_id)
