"""Offline E2E: the database agent path runs without network and never claims Live."""

import asyncio
import json

import httpx
from fastapi.testclient import TestClient
from prototypes.adaptive_assessment.live.budget import OfflineSnapshotPermit
from prototypes.adaptive_assessment.live.demo import MOCK_BASE, native_response
from prototypes.adaptive_assessment.live.pricing import RESEARCH_PRICE_20261104 as PRICE
from prototypes.capability_profiles.agent import ProfileMockPolicy
from prototypes.durable_teacher_agent.models import TaskCreate
from prototypes.durable_teacher_agent.service import DurableBudget
from prototypes.qwen_execution import DatabaseTaskInput
from prototypes.qwen_execution.testing import run_database_profiles_offline
from pydantic import SecretStr

from app.ai.edu.qwen_config import QwenSettings

from .test_tasks import QUESTION, actor, setup, upload


def test_offline_database_agent_runs_without_network(tmp_path, monkeypatch):
    fixture, service = setup(tmp_path)
    client = TestClient(fixture.app)
    client.headers["Authorization"] = "Bearer " + fixture.token
    batch = upload(client, fixture)
    teacher = actor(fixture, service)
    service.store.grant(teacher, batch["batch_id"], batch["snapshot_id"])
    body = TaskCreate(
        snapshot_id=batch["snapshot_id"], question=QUESTION, idempotency_key="offline-e2e-01"
    )
    row, _ = service.store.create(teacher, batch["batch_id"], body)
    service.store.start(row["task_id"])

    real_client = httpx.AsyncClient

    def guarded(*args, **kwargs):
        if not isinstance(kwargs.get("transport"), httpx.MockTransport):
            raise AssertionError("offline run must not open a real network client")
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", guarded)

    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        view = json.loads(payload["messages"][-1]["content"])
        return httpx.Response(200, json=native_response(ProfileMockPolicy().choose(view)))

    source = service.source(row, teacher, running=True)
    permit = OfflineSnapshotPermit(source, row["campaign_id"], PRICE.fingerprint)
    budget = DurableBudget(permit, service.directory, service.store, row["task_id"])
    try:
        binding = DatabaseTaskInput(
            task_id=row["task_id"],
            batch_id=batch["batch_id"],
            snapshot_id=batch["snapshot_id"],
            actor=teacher,
            store=service.store,
            source=source,
            budget=budget,
            settings=QwenSettings(SecretStr("offline-placeholder"), MOCK_BASE),
            require_externalization=lambda scope: None,
        )
        raw, book, executor = asyncio.run(
            run_database_profiles_offline(
                binding,
                limits=service.profile_limits,
                transport=httpx.MockTransport(handler),
            )
        )
        assert captured, "the agent must have produced at least one model request"
        assert raw["origin"] == "http_mock" and raw["real_qwen"] == "not_run"
        assert executor.facts, "the agent must publish verified facts"
        assert "qwen_live" not in json.dumps(raw)
    finally:
        budget.close()
