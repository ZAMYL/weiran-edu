"""Production app factory: the controlled create_app path with Live wiring.

Start with:
    uvicorn app.production:create_production_app --factory

Execution belongs to app.worker_main; the HTTP process only enqueues.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from prototypes.durable_teacher_agent.service import TaskService

from app.core.config import Settings
from app.main import create_app

REPOSITORY = Path(__file__).resolve().parents[3]


def _outside_repository(raw: str, name: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"{name} must be absolute")
    resolved = path.resolve()
    if resolved.is_relative_to(REPOSITORY):
        raise RuntimeError(f"{name} must stay outside the repository")
    return resolved


def build_production() -> tuple[FastAPI, TaskService]:
    settings = Settings(
        environment="production",
        database_path=_outside_repository(os.environ["WEIRAN_EDU_DATABASE"], "WEIRAN_EDU_DATABASE"),
        ai_task_routes=False,  # create_app installs the real router from approval env
    )
    application = create_app(settings)
    service = getattr(application.state, "ai_task_service", None)
    if service is None:
        raise RuntimeError("real_data_approval_not_configured")
    return application, service


def create_production_app() -> FastAPI:
    application, _ = build_production()
    return application


def build_programming_worker():
    """Build the production programming service without requiring five-dim approval."""
    settings = Settings(
        environment="production",
        database_path=_outside_repository(os.environ["WEIRAN_EDU_DATABASE"], "WEIRAN_EDU_DATABASE"),
        ai_task_routes=False,
    )
    application = create_app(settings)
    service = getattr(application.state, "programming_tasks", None)
    if service is None:
        raise RuntimeError("programming_service_not_configured")
    if service.runner is None:
        raise RuntimeError("programming_live_approval_not_configured")
    return service
