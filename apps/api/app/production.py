"""Production app factory: real data admission + durable task service.

Start with:
    uvicorn app.production:create_production_app --factory

Execution belongs to app.worker_main; the HTTP process only enqueues.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from prototypes.durable_teacher_agent.api import install_production
from prototypes.durable_teacher_agent.service import TaskService
from prototypes.durable_teacher_agent.store import TaskStore
from prototypes.teacher_agent_bridge.provider import (
    ProductionDatabaseProvider,
    RealDataAdmission,
)

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
        ai_task_routes=False,  # the real router is installed below, never the 503 skeleton
    )
    application = create_app(settings)
    approval_path = os.environ.get("WEIRAN_EDU_REAL_DATA_APPROVAL")
    if not approval_path:
        raise RuntimeError("WEIRAN_EDU_REAL_DATA_APPROVAL is required")
    admission = RealDataAdmission.load(approval_path)
    state = _outside_repository(
        os.environ.get(
            "WEIRAN_EDU_AI_STATE_DIR", str(Path(settings.database_path).parent / "ai-state")
        ),
        "WEIRAN_EDU_AI_STATE_DIR",
    )
    state.mkdir(parents=True, exist_ok=True)
    provider = ProductionDatabaseProvider(application.state.edu_service, admission)
    store = TaskStore(provider)
    store.initialize()
    service = TaskService(provider, store, state)
    install_production(application, service)
    return application, service


def create_production_app() -> FastAPI:
    application, _ = build_production()
    return application
