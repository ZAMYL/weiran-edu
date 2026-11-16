"""Task events must expose real phases without leaking payload internals."""

from fastapi.testclient import TestClient

from .test_tasks import QUESTION, actor, setup, upload


def build(tmp_path):
    fixture, service = setup(tmp_path)
    client = TestClient(fixture.app)
    client.headers["Authorization"] = "Bearer " + fixture.token
    batch = upload(client, fixture)
    service.store.grant(actor(fixture, service), batch["batch_id"], batch["snapshot_id"])
    base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
    task = client.post(
        base,
        json={
            "snapshot_id": batch["snapshot_id"],
            "question": QUESTION,
            "idempotency_key": "events-01",
        },
    ).json()
    path = base + "/" + task["task_id"]
    client.get(path, params={"snapshot_id": batch["snapshot_id"]})
    return fixture, service, client, batch, path


def test_events_report_real_phases_without_payload_text(tmp_path):
    fixture, service, client, batch, path = build(tmp_path)
    response = client.get(path + "/events", params={"snapshot_id": batch["snapshot_id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_id"] and body["events"]
    phases = [event["phase"] for event in body["events"]]
    assert any(phase.startswith("tool_") or phase == "result_committed" for phase in phases)
    for event in body["events"]:
        assert set(event) == {"sequence", "phase", "at", "detail"}
        assert set(event["detail"]) <= {
            "tool",
            "status",
            "error",
            "request_count",
            "model_requests",
            "tool_attempts",
            "charged_tokens",
            "charged_cny_upper_bound",
            "code",
            "fact_count",
        }


def test_events_are_incremental_and_ordered(tmp_path):
    fixture, service, client, batch, path = build(tmp_path)
    first = client.get(path + "/events", params={"snapshot_id": batch["snapshot_id"]}).json()
    sequences = [event["sequence"] for event in first["events"]]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    last = sequences[-1]
    rest = client.get(
        path + "/events", params={"snapshot_id": batch["snapshot_id"], "after": last}
    ).json()
    assert all(event["sequence"] > last for event in rest["events"])


def test_events_require_the_owning_snapshot(tmp_path):
    fixture, service, client, batch, path = build(tmp_path)
    other = client.get(path + "/events", params={"snapshot_id": "not-the-snapshot"})
    assert other.status_code in (404, 409)
