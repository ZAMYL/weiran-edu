"""Real process timeout/cancellation cleanup plus formal HTTP error behavior."""

import asyncio
import json
import multiprocessing
import time

import pytest

from app.analysis.edu import AssessmentRecord, Dimension, ScorePolicy, prepare_dataset
from app.analysis.edu.discovery import Policy
from app.core.errors import APIError
from app.edu import discovery_bridge, discovery_worker

from .test_discovery_api import query
from .test_insights_api import formal_batch as formal_batch
from .worker_process_targets import crashed_child


def sleeping_child(*args):
    time.sleep(30)


def arguments():
    # The discovery core requires the same 0.01 grid as formal data.
    from fractions import Fraction

    data = prepare_dataset(
        (
            AssessmentRecord(
                "synthetic-batch",
                "synthetic-student",
                "synthetic-record",
                "synthetic-source",
                {d: 3 for d in Dimension},
            ),
        ),
        policy=ScorePolicy(step=Fraction(1, 100)),
    )
    return (data, "synthetic-batch", "synthetic-snapshot", ("synthetic-student",), (), Policy())


def child_ids():
    return {p.pid for p in multiprocessing.active_children()}


def test_timeout_stops_real_child_and_next_request_works(monkeypatch):
    before = child_ids()
    original = discovery_worker.compute_child
    monkeypatch.setattr(discovery_worker, "compute_child", sleeping_child)
    started = time.perf_counter()
    with pytest.raises(APIError) as error:
        asyncio.run(discovery_worker.execute(*arguments(), timeout=0.5))
    assert error.value.code == "DISCOVERY_TIMEOUT"
    assert time.perf_counter() - started < 5
    assert child_ids() == before
    monkeypatch.setattr(discovery_worker, "compute_child", original)
    result = asyncio.run(discovery_worker.execute(*arguments()))
    assert result.findings == () and child_ids() == before


@pytest.mark.parametrize("method", ["disconnect", "cancel"])
def test_disconnect_and_task_cancellation_reap_real_child(monkeypatch, method):
    before = child_ids()
    monkeypatch.setattr(discovery_worker, "compute_child", sleeping_child)

    async def run():
        started = time.perf_counter()

        async def disconnected():
            return time.perf_counter() - started > 0.15

        task = asyncio.create_task(
            discovery_worker.execute(
                *arguments(), disconnected=disconnected if method == "disconnect" else None
            )
        )
        if method == "cancel":
            await asyncio.sleep(0.15)
            task.cancel()
        if method == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(APIError) as error:
                await task
            assert error.value.status_code == 499 and error.value.code == "DISCOVERY_CANCELLED"

    asyncio.run(run())
    assert child_ids() == before


def test_crashed_worker_is_unavailable_not_empty_discovery(monkeypatch):
    before = child_ids()
    monkeypatch.setattr(discovery_worker, "compute_child", crashed_child)
    with pytest.raises(APIError) as error:
        asyncio.run(discovery_worker.execute(*arguments(), timeout=5))
    assert error.value.code == "DISCOVERY_UNAVAILABLE"
    assert child_ids() == before


def test_http_busy_budget_and_real_hard_timeout_are_explicit(client, formal_batch, monkeypatch):
    base, batch, _ = formal_batch(17)
    assert discovery_bridge.SLOTS.acquire(blocking=False)
    assert discovery_bridge.SLOTS.acquire(blocking=False)
    try:
        response = query(client, base, batch)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DISCOVERY_BUSY"
    finally:
        discovery_bridge.SLOTS.release()
        discovery_bridge.SLOTS.release()
    monkeypatch.setattr(discovery_bridge, "MAX_RECORDS", 1)
    assert query(client, base, batch).json()["error"]["code"] == "DISCOVERY_LIMIT_EXCEEDED"
    monkeypatch.setattr(discovery_bridge, "MAX_RECORDS", 1666)
    monkeypatch.setattr(discovery_bridge, "MAX_SECONDS", 0.5)
    monkeypatch.setattr(discovery_worker, "compute_child", sleeping_child)
    before = child_ids()
    response = query(client, base, batch)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DISCOVERY_TIMEOUT"
    assert "items" not in response.json() and child_ids() == before
    # All compute capacity must be returned even after the request fails.
    assert discovery_bridge.SLOTS.acquire(blocking=False)
    assert discovery_bridge.SLOTS.acquire(blocking=False)
    discovery_bridge.SLOTS.release()
    discovery_bridge.SLOTS.release()


def test_actual_asgi_disconnect_reaps_process_and_releases_capacity(
    client, runtime, formal_batch, monkeypatch
):
    base, batch, _ = formal_batch(17)
    before = child_ids()
    monkeypatch.setattr(discovery_worker, "compute_child", sleeping_child)
    original = discovery_worker.execute
    messages = []

    async def scenario():
        started = asyncio.Event()
        consumed = False

        async def execute(*args, **kwargs):
            started.set()
            return await original(*args, **kwargs)

        monkeypatch.setattr(discovery_worker, "execute", execute)
        body = json.dumps({"snapshot_id": batch["snapshot_id"]}).encode()

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        path = base + "/discoveries/query"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", ("Bearer " + runtime.token).encode()),
            ],
        }
        await asyncio.wait_for(client.app(scope, receive, send), timeout=8)

    asyncio.run(scenario())
    assert child_ids() == before
    assert discovery_bridge.SLOTS.acquire(blocking=False)
    assert discovery_bridge.SLOTS.acquire(blocking=False)
    discovery_bridge.SLOTS.release()
    discovery_bridge.SLOTS.release()
    assert all(b'"items"' not in message.get("body", b"") for message in messages)
