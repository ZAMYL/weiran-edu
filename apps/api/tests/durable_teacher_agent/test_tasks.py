"""Synthetic real DB + real HTTP auth + native Function Calling Mock integration."""

import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from prototypes.durable_teacher_agent.api import install
from prototypes.durable_teacher_agent.models import RESULT_CONTRACT_KEY
from prototypes.durable_teacher_agent.store import encode
from prototypes.teacher_agent_bridge.fixtures import build_host
from prototypes.teacher_agent_bridge.provider import DatabaseProvider, digest

from app.edu.auth import authenticate
from app.importing.edu import parse_excel

QUESTION = "比较 算法与数据结构、代码实现能力 的组合与 问题建模能力 的群体差异"


def setup(directory):
    fixture = build_host(directory, install_bridge=False)
    fixture.app.state.edu_service.parser = parse_excel
    provider = DatabaseProvider(fixture.app.state.edu_service, tuple(fixture.books.values()))
    service = install(fixture.app, provider, directory / "campaigns")
    return fixture, service


def upload(client, fixture, case="normal"):
    response = client.post(
        "/api/v1/edu/imports/preview",
        files={
            "file": (
                "synthetic.xlsx",
                fixture.books[case],
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 201, response.text
    p = response.json()["import"]
    response = client.post(
        f"/api/v1/edu/imports/{p['import_id']}/commit",
        json={"preview_version": p["preview_version"], "acknowledge_warnings": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


def actor(fixture, service):
    return authenticate(service.store.database, fixture.token)


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    directory = tmp_path_factory.mktemp("durable-investigation")
    fixture, service = setup(directory)
    with TestClient(fixture.app) as client:
        client.headers["Authorization"] = "Bearer " + fixture.token
        b = upload(client, fixture)
        gid = service.store.grant(actor(fixture, service), b["batch_id"], b["snapshot_id"])
        base = f"/api/v1/edu/batches/{b['batch_id']}/ai-investigation-tasks"
        body = {
            "snapshot_id": b["snapshot_id"],
            "question": QUESTION,
            "idempotency_key": "synthetic-once-01",
        }
        response = client.post(base, json=body)
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        path = base + "/" + task_id
        response = client.get(path, params={"snapshot_id": b["snapshot_id"]})
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["state"] == "completed", view
        yield fixture, service, client, b, gid, base, path, body, view


def test_persist_reload_evidence_review_and_report(completed):
    fixture, service, client, b, gid, base, path, body, view = completed
    assert view["model_requests"] == 3 and view["tool_attempts"] == 3
    assert view["result"]["real_qwen"] == "not_run"
    # Install a fresh host over the same SQLite/file journals, without old memory cache.
    from prototypes.durable_teacher_agent.service import TaskService
    from prototypes.durable_teacher_agent.store import TaskStore

    restarted = TaskService(service.provider, TaskStore(service.provider), service.directory)
    restored = restarted.view(
        actor(fixture, service), b["batch_id"], view["task_id"], b["snapshot_id"]
    )
    assert restored.model_dump(mode="json") == view
    group = next(f for f in view["result"]["facts"] if f["method"] == "compare_student_groups")
    m = {m["key"]: m for m in group["metrics"]}
    assert m["metrics.target_count"]["number"] == 26
    assert m["metrics.remainder_count"]["number"] == 27
    assert m["metrics.difference"]["exact"] == {"numerator": -4, "denominator": 1}
    source = next(s for s in group["source_refs"] if s["metric_key"] == "target.valid")
    response = client.post(source["records_path"], json=source["records_query"] | {"limit": 2})
    assert response.status_code == 200, response.text
    records = response.json()
    assert records["total_count"] == 26 and len(records["items"]) == 2
    for link in records["items"]:
        response = client.get(link["evidence_path"], params={"snapshot_id": b["snapshot_id"]})
        assert response.status_code == 200 and response.json()["score"] == 1
        assert response.json()["comment"] is None
    relation = next(f for f in view["result"]["facts"] if f["method"] == "query_dimension_relation")
    submission = {
        "snapshot_id": b["snapshot_id"],
        "fact_ids": [relation["fact_id"], group["fact_id"]],
    }
    response = client.post(path + "/review-submissions", json=submission)
    assert response.status_code == 200, response.text
    receipt = response.json()
    response = client.post(path + "/review-submissions", json=submission)
    assert response.status_code == 200 and response.json() == receipt
    response = client.get(receipt["result_path"], params={"snapshot_id": b["snapshot_id"]})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["version"] == 1
    assert saved["discovery_findings"][0]["teacher_review"]["status"] == "pending"
    response = client.patch(
        receipt["review_path"],
        json={
            "snapshot_id": b["snapshot_id"],
            "expected_version": 1,
            "decisions": [
                {"candidate_id": item, "status": "confirmed"} for item in receipt["candidate_ids"]
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    for fmt in ("md", "xlsx"):
        response = client.get(
            receipt["export_path"],
            params={"snapshot_id": b["snapshot_id"], "expected_version": 2, "format": fmt},
        )
        assert response.status_code == 200, response.text[:200]
        assert response.headers["cache-control"] == "no-store"
        if fmt == "xlsx":
            book = load_workbook(BytesIO(response.content), data_only=False)
            assert len(book.sheetnames) >= 4
            text = " ".join(
                str(cell.value)
                for sheet in book
                for row in sheet
                for cell in row
                if cell.value is not None
            )
            book.close()
        else:
            text = response.text
        assert "53" in text and "相关" in text
        assert "完全合成-normal" not in text and "合成学生" not in text
    # Original review version gates still apply; stale export cannot succeed.
    response = client.get(
        receipt["export_path"],
        params={"snapshot_id": b["snapshot_id"], "expected_version": 1, "format": "md"},
    )
    assert response.status_code == 409


def test_legacy_standard_result_stays_compatible_and_cannot_self_upgrade(completed):
    fixture, service, client, batch, _, _, path, _, view = completed
    task_id = view["task_id"]
    before = service.store.get(actor(fixture, service), task_id)
    request = json.loads(before["request_json"])
    assert request.pop(RESULT_CONTRACT_KEY) == "investigation-v1"
    try:
        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_tasks SET request_json=?,request_sha=? WHERE task_id=?",
                (encode(request), digest(request), task_id),
            )
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
        assert response.status_code == 200, response.text
        assert "profile_projection" not in response.json()["result"]

        forged = json.loads(before["result_json"])
        forged["profile_projection"] = {}
        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_tasks SET result_json=?,result_sha=? WHERE task_id=?",
                (encode(forged), digest(forged), task_id),
            )
        response = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "AI_PROFILE_PROOF_CONFLICT"
    finally:
        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_tasks SET request_json=?,request_sha=?,result_json=?,"
                "result_sha=? WHERE task_id=?",
                (
                    before["request_json"],
                    before["request_sha"],
                    before["result_json"],
                    before["result_sha"],
                    task_id,
                ),
            )


def test_idempotence_new_request_cannot_reset_budget(completed):
    _, _, client, b, _, base, path, body, view = completed
    response = client.post(base, json=body)
    assert response.status_code == 202 and response.json()["task_id"] == view["task_id"]
    response = client.post(base, json=body | {"idempotency_key": "synthetic-new-id"})
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "AI_CAMPAIGN_ALREADY_USED"
    )
    response = client.post(base, json=body | {"question": "核查缺分"})
    assert response.status_code == 409
    response = client.get(path, params={"snapshot_id": "wrong-snapshot"})
    assert response.status_code == 409


def test_unknown_and_unqualified_fact_not_promoted(completed):
    _, _, client, b, _, _, path, _, view = completed
    for ids in (["invented-fact"], [view["result"]["facts"][0]["fact_id"]]):
        response = client.post(
            path + "/review-submissions", json={"snapshot_id": b["snapshot_id"], "fact_ids": ids}
        )
        assert response.status_code == 422


def test_review_submit_recovers_after_timeout_and_never_duplicates(tmp_path, monkeypatch):
    """A lost/timed-out review submit must not strand the claim nor create duplicate reports."""
    from app.core.errors import APIError
    from app.edu.discovery_results import DiscoveryResultService

    fixture, service = setup(tmp_path)
    with TestClient(fixture.app) as client:
        client.headers["Authorization"] = "Bearer " + fixture.token
        b = upload(client, fixture)
        service.store.grant(actor(fixture, service), b["batch_id"], b["snapshot_id"])
        base = f"/api/v1/edu/batches/{b['batch_id']}/ai-investigation-tasks"
        response = client.post(
            base,
            json={
                "snapshot_id": b["snapshot_id"],
                "question": QUESTION,
                "idempotency_key": "review-recover-01",
            },
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        path = base + "/" + task_id
        response = client.get(path, params={"snapshot_id": b["snapshot_id"]})
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "completed", response.text
        view = response.json()
        relation = next(f for f in view["result"]["facts"] if f["method"] == "query_dimension_relation")
        group = next(f for f in view["result"]["facts"] if f["method"] == "compare_student_groups")
        submission = {
            "snapshot_id": b["snapshot_id"],
            "fact_ids": [relation["fact_id"], group["fact_id"]],
        }

        async def fail_once(self, principal, batch_id, body, *, request=None):
            raise APIError(499, "DISCOVERY_CANCELLED", "simulated client timeout")

        original = DiscoveryResultService.create_discovery
        monkeypatch.setattr(DiscoveryResultService, "create_discovery", fail_once)
        first = client.post(path + "/review-submissions", json=submission)
        assert first.status_code == 499, first.text
        monkeypatch.setattr(DiscoveryResultService, "create_discovery", original)
        # The interrupted claim must be cleared, leaving no report behind.
        with service.store.database.transaction() as connection:
            assert service.store._row(connection, task_id)["review_json"] is None
            assert (
                connection.execute(
                    "SELECT count(*) FROM analysis_results WHERE batch_id=?", (b["batch_id"],)
                ).fetchone()[0]
                == 0
            )

        retry = client.post(path + "/review-submissions", json=submission)
        assert retry.status_code == 200, retry.text
        with service.store.database.transaction() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM analysis_results WHERE batch_id=?", (b["batch_id"],)
                ).fetchone()[0]
                == 1
            )


def test_readback_rejects_purpose_revoked_during_witness_replay(completed, monkeypatch):
    fixture, service, client, b, gid, base, path, body, view = completed
    group = next(f for f in view["result"]["facts"] if f["method"] == "compare_student_groups")
    original = service.provider.replay_witnesses

    def revoke_during_replay(source, references):
        witnesses = original(source, references)
        service.store.revoke(gid)
        return witnesses

    monkeypatch.setattr(service.provider, "replay_witnesses", revoke_during_replay)
    response = client.post(
        path + "/records/query",
        json={
            "snapshot_id": b["snapshot_id"],
            "fact_id": group["fact_id"],
            "metric_key": "target.valid",
        },
    )
    assert response.status_code == 403
    response = client.get(path, params={"snapshot_id": b["snapshot_id"]})
    assert response.status_code == 403
    response = client.post(base, json=body)
    assert response.status_code == 403
