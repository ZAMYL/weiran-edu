"""Real ASGI cancellation during CPU work, source checks and workbook construction."""

import asyncio
import threading
from urllib.parse import urlencode

import pytest

from app.core.errors import APIError
from app.edu import discovery_bridge, discovery_results, discovery_worker
from app.reporting.edu import render

from .test_discovery_api import query
from .test_discovery_results import save
from .test_discovery_worker import child_ids, sleeping_child
from .test_insights_api import formal_batch as formal_batch

CASES = [
    ("save", "compute", "md"),
    *(
        (op, "compute", fmt)
        for op in ("export", "export-check")
        for fmt in ("json", "zip", "xlsx", "md")
    ),
    *((op, "sources", "md") for op in ("save", "export", "export-check")),
    *((op, "render", "xlsx") for op in ("export", "export-check")),
    *((op, "task", "md") for op in ("save", "export", "export-check")),
]


@pytest.mark.parametrize("operation,phase,format", CASES)
def test_disconnect_stops_work_and_releases_resources(
    client, runtime, formal_batch, monkeypatch, operation, phase, format
):
    import json

    base, batch, _ = formal_batch(17)
    listed = query(client, base, batch, limit=1).json()
    saved = save(client, base, batch, listed).json()
    baseline = child_ids()
    started, disconnected, finished = (threading.Event() for _ in range(3))
    messages, errors, closed = [], [], []
    scope = {}
    original_execute = discovery_worker.execute
    original_child = discovery_worker.compute_child

    def pause_until_cancelled():
        started.set()
        assert scope["state"]["edu_discovery_cancelled"].wait(4)

    if phase in {"compute", "task"}:
        monkeypatch.setattr(discovery_worker, "compute_child", sleeping_child)

        async def computing(*args, **kwargs):
            started.set()
            try:
                return await original_execute(*args, **kwargs)
            except APIError as error:
                errors.append(error.code)
                raise
            finally:
                finished.set()

        monkeypatch.setattr(discovery_worker, "execute", computing)
    elif phase == "sources":
        original = discovery_results.checked_findings

        def validating(*args, **kwargs):
            pause_until_cancelled()
            try:
                return original(*args, **kwargs)
            except APIError as error:
                errors.append(error.code)
                raise
            finally:
                finished.set()

        monkeypatch.setattr(discovery_results, "checked_findings", validating)
    else:
        original_text, original_workbook = render.text_cell, render.Workbook
        interrupted = False

        def workbook():
            result = original_workbook()
            close = result.close

            def closing():
                closed.append(True)
                close()

            result.close = closing
            return result

        def during_workbook(*args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                pause_until_cancelled()
            return original_text(*args, **kwargs)

        original_xlsx = render._xlsx

        def rendering(*args, **kwargs):
            try:
                return original_xlsx(*args, **kwargs)
            except APIError as error:
                errors.append(error.code)
                raise
            finally:
                finished.set()

        monkeypatch.setattr(render, "Workbook", workbook)
        monkeypatch.setattr(render, "text_cell", during_workbook)
        monkeypatch.setattr(render, "_xlsx", rendering)

    async def scenario():
        consumed = False
        params = {"snapshot_id": batch["snapshot_id"], "expected_version": 1, "format": format}
        if operation == "save":
            path, method, query_string = base + "/analysis-results", "POST", b""
            body = json.dumps(
                {
                    "snapshot_id": batch["snapshot_id"],
                    "include_dimensions": False,
                    "title": "合成断连回归",
                    "discovery": {
                        "input_key": listed["scope"]["input_key"],
                        "discovery_ids": [listed["items"][0]["discovery_id"]],
                    },
                }
            ).encode()
        else:
            path = base + "/analysis-results/" + saved["result_id"] + "/" + operation
            method = "GET" if operation == "export" else "POST"
            query_string = urlencode(params).encode() if method == "GET" else b""
            body = json.dumps(params).encode() if method == "POST" else b""

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            while not disconnected.is_set():
                await asyncio.sleep(0.005)
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        scope.update(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": query_string,
                "root_path": "",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 1234),
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"authorization", ("Bearer " + runtime.token).encode()),
                ],
            }
        )
        task = asyncio.create_task(client.app(scope, receive, send))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            if phase == "task":
                task.cancel()
            else:
                disconnected.set()
            # Production budget remains 15s. Finishing here proves cancellation,
            # rather than the CPU timeout, stops the real child and parent pipeline.
            assert await asyncio.to_thread(finished.wait, 4)
            if phase == "task":
                await asyncio.gather(task, return_exceptions=True)
            else:
                await asyncio.wait_for(task, 5)
        finally:
            disconnected.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert errors == ["DISCOVERY_CANCELLED"] or (phase == "task" and not errors)
    assert child_ids() == baseline
    if phase == "render":
        assert closed == [True]
    assert not any(
        key.lower() == b"content-disposition"
        for message in messages
        for key, _ in message.get("headers", [])
    )
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 1
    assert discovery_bridge.SLOTS.acquire(blocking=False)
    try:
        assert discovery_bridge.SLOTS.acquire(blocking=False)
        discovery_bridge.SLOTS.release()
    finally:
        discovery_bridge.SLOTS.release()
    monkeypatch.setattr(discovery_worker, "execute", original_execute)
    monkeypatch.setattr(discovery_worker, "compute_child", original_child)
    assert query(client, base, batch, limit=1).status_code == 200


@pytest.mark.parametrize("operation", ["save", "export-check"])
def test_disconnect_before_json_complete_returns_cancelled_without_computation(
    client, runtime, formal_batch, monkeypatch, operation
):
    base, batch, _ = formal_batch(17)
    listed = query(client, base, batch, limit=1).json()
    saved = save(client, base, batch, listed).json()

    async def forbidden(*args, **kwargs):
        raise AssertionError("No worker may start for an incomplete disconnected request")

    monkeypatch.setattr(discovery_worker, "execute", forbidden)
    messages = []

    async def scenario():
        consumed = False

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": b'{"snapshot_id":', "more_body": True}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        path = base + "/analysis-results"
        if operation != "save":
            path += "/" + saved["result_id"] + "/export-check"
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
        await asyncio.wait_for(client.app(scope, receive, send), 5)

    asyncio.run(scenario())
    assert any(m.get("status") == 499 for m in messages)
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 1
