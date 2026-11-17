"""Install the controlled Live TaskService into the formal FastAPI app.

This module is the single bridge used by ``create_app`` and the production
factory. It is never imported by the synthetic test host.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from prototypes.durable_teacher_agent.api import install_production
from prototypes.durable_teacher_agent.service import TaskService
from prototypes.durable_teacher_agent.store import TaskStore
from prototypes.qwen_execution import code_fingerprint
from prototypes.teacher_agent_bridge.provider import (
    ProductionDatabaseProvider,
    RealDataAdmission,
)

REPOSITORY = Path(__file__).resolve().parents[3]


def _outside_repository(raw: str, name: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"{name} must be absolute")
    resolved = path.resolve()
    if resolved.is_relative_to(REPOSITORY):
        raise RuntimeError(f"{name} must stay outside the repository")
    return resolved


def install_live_from_env(application: FastAPI) -> TaskService:
    settings = application.state.settings
    if settings.environment != "production":
        raise RuntimeError("live_wiring_requires_production")
    if settings.database_path is None:
        raise RuntimeError("live_wiring_requires_database")
    approval_path = os.environ.get("WEIRAN_EDU_REAL_DATA_APPROVAL")
    if not approval_path:
        raise RuntimeError("WEIRAN_EDU_REAL_DATA_APPROVAL is required")
    admission = RealDataAdmission.load(approval_path, code_sha256=code_fingerprint())
    state = _outside_repository(
        os.environ.get(
            "WEIRAN_EDU_AI_STATE_DIR",
            str(Path(settings.database_path).parent / "ai-state"),
        ),
        "WEIRAN_EDU_AI_STATE_DIR",
    )
    state.mkdir(parents=True, exist_ok=True)
    provider = ProductionDatabaseProvider(application.state.edu_service, admission)
    store = TaskStore(provider)
    store.initialize()
    service = TaskService(provider, store, state)
    install_production(application, service)
    return service
