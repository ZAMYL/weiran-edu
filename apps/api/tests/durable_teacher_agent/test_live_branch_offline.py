"""The live branch, exercised end to end with the network intercepted.

The approval must bind the task's own campaign id, so it is written after the
task exists; that ordering is also what an operator has to follow.
"""

import json
from hashlib import sha256

import httpx
from fastapi.testclient import TestClient
from prototypes.adaptive_assessment.live.demo import MOCK_BASE, native_response
from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261112 as PRICE
from prototypes.capability_profiles.agent import ProfileMockPolicy
from prototypes.durable_teacher_agent import live_admission
from prototypes.durable_teacher_agent.models import TaskCreate

from .test_tasks import QUESTION, actor, setup, upload

FINGERPRINT = "f" * 64


def approval_for(campaign_id, source_sha):
    return {
        "campaign_id": campaign_id,
        "human_approval_reference": "owner-approval-offline-01",
        "purpose": "database_live",
        "model": PRICE.model,
        "endpoint_sha256": sha256(MOCK_BASE.encode()).hexdigest(),
        "source_fingerprints": {source_sha: source_sha},
        "price_fingerprint": PRICE.fingerprint,
        "valid_until": PRICE.valid_until,
        "max_requests": 6,
        "max_output_tokens": 800,
        "total_token_limit": 5_955_648,
        "cost_cap_cny": "4.78",
        "independent_review_reference": "offline-review-01",
        "reviewed_code_sha256": FINGERPRINT,
    }


def test_live_branch_reaches_the_agent_and_persists_provenance(tmp_path, monkeypatch):
    fixture, service = setup(tmp_path)
    client = TestClient(fixture.app)
    client.headers["Authorization"] = "Bearer " + fixture.token
    batch = upload(client, fixture)
    teacher = actor(fixture, service)
    service.store.grant(teacher, batch["batch_id"], batch["snapshot_id"])
    with service.store.database.transaction(write=True) as connection:
        connection.execute("UPDATE ai_synthetic_grants SET purpose='database_live'")
        source_sha = connection.execute(
            "SELECT source_sha FROM ai_synthetic_grants LIMIT 1"
        ).fetchone()[0]

    row, _ = service.store.create(
        teacher,
        batch["batch_id"],
        TaskCreate(
            snapshot_id=batch["snapshot_id"], question=QUESTION, idempotency_key="live-e2e-01"
        ),
    )

    ledger = tmp_path / "ledger"
    approval = tmp_path / "approval.json"
    approval.write_text(
        json.dumps(approval_for(row["campaign_id"], source_sha)), encoding="utf-8"
    )
    monkeypatch.setattr(live_admission, "code_fingerprint", lambda: FINGERPRINT)
    monkeypatch.setenv("WEIRAN_EDU_QWEN_LIVE_APPROVAL", str(approval))
    monkeypatch.setenv("WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", str(ledger))
    monkeypatch.setenv("DASHSCOPE_BASE_URL", MOCK_BASE)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "offline-placeholder")

    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        view = json.loads(payload["messages"][-1]["content"])
        return httpx.Response(200, json=native_response(ProfileMockPolicy().choose(view)))

    real_client = httpx.AsyncClient

    def intercepted(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", intercepted)

    service.execute(row["task_id"])

    stored = service.store.get(teacher, row["task_id"])
    assert stored["state"] == "completed", (stored["state"], stored["error_code"])
    assert calls, "the live branch must have sent at least one request"
    provenance = json.loads(stored["provenance_json"])
    assert provenance["origin"] == "qwen_live"
    assert provenance["state"] == "completed"
    assert provenance["request_state"] == "responses_received"
    assert provenance["model_requests"] == len(calls)
    assert (ledger / (row["campaign_id"] + ".jsonl")).is_file()