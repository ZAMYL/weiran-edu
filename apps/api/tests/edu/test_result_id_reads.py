"""Malformed discoveries stay async; missing result writes never take a write lock."""

import asyncio
import threading
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

from app.edu.discoveries import DiscoveryReader

from .test_discovery_api import query
from .test_insights_api import formal_batch as formal_batch


@pytest.mark.parametrize("invalid_id", [False, True])
def test_discovery_id_authorization_does_not_block_event_loop(
    client, runtime, formal_batch, monkeypatch, invalid_id
):
    base, batch, _ = formal_batch(17)
    card = query(client, base, batch, limit=1).json()["items"][0]
    identifier = "not-a-discovery-id" if invalid_id else card["discovery_id"]
    original = DiscoveryReader.query
    acquired, released, health_done = (threading.Event() for _ in range(3))
    holder = None

    def lock_database():
        connection = runtime.db._connect()
        try:
            connection.execute("BEGIN EXCLUSIVE")
            acquired.set()
            health_done.wait(2)
            connection.rollback()
        finally:
            connection.close()
            released.set()

    async def while_locked(self, *args, **kwargs):
        nonlocal holder
        holder = threading.Thread(target=lock_database)
        holder.start()
        assert await asyncio.to_thread(acquired.wait, 5)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(DiscoveryReader, "query", while_locked)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer " + runtime.token},
        ) as session:
            request = asyncio.create_task(
                session.post(
                    base + "/discoveries/" + identifier + "/query",
                    json={"snapshot_id": batch["snapshot_id"]},
                )
            )
            try:
                assert await asyncio.to_thread(acquired.wait, 5)
                health = await session.get("/api/v1/health")
                assert health.status_code == 200
                assert not released.is_set(), "Health was blocked until SQLite released its lock"
            finally:
                health_done.set()
                response = await request
            assert response.status_code == (404 if invalid_id else 200), response.text

    try:
        asyncio.run(scenario())
    finally:
        health_done.set()
        if holder is not None:
            holder.join(5)


@pytest.mark.parametrize("result_id", ["not-an-analysis-id", str(uuid4())])
@pytest.mark.parametrize("reader", [False, True])
def test_missing_result_patch_checks_access_without_requesting_a_write_lock(
    client, runtime, formal_batch, monkeypatch, result_id, reader
):
    base, batch, _ = formal_batch(17)
    if reader:
        runtime.admin.grant_read(runtime.other, batch["batch_id"])
        client.headers["Authorization"] = "Bearer " + runtime.other_token
    locker = runtime.db._connect()
    connect = runtime.db._connect
    write_attempts = []

    def traced():
        connection = connect()
        connection.set_trace_callback(
            lambda sql: write_attempts.append(True) if sql == "BEGIN IMMEDIATE" else None
        )
        return connection

    locker.execute("BEGIN IMMEDIATE")  # Reads remain available, a second writer cannot start.
    monkeypatch.setattr(runtime.db, "_connect", traced)
    try:
        response = client.patch(
            base + "/analysis-results/" + result_id,
            json={
                "snapshot_id": batch["snapshot_id"],
                "expected_version": 1,
                "note": "合成核验",
            },
        )
        assert response.status_code == (403 if reader else 404), response.text
        assert not write_attempts
    finally:
        locker.rollback()
        locker.close()


@pytest.mark.parametrize("change,expected", [("token", 401), ("scope", 404), ("version", 409)])
def test_patch_rechecks_permissions_and_version_after_read_preflight(
    client, runtime, formal_batch, monkeypatch, change, expected
):
    from app.edu.results import ResultService
    from app.edu.review_models import AnalysisPatch

    from .test_reviewed_results import save_result

    base, batch, _ = formal_batch(17)
    saved = save_result(client, base, batch).json()
    transaction = runtime.db.transaction
    mutated = False

    @contextmanager
    def racing(*, write=False):
        nonlocal mutated
        if write and not mutated:
            mutated = True
            if change == "token":
                runtime.admin.revoke_tokens(runtime.teacher)
            elif change == "scope":
                runtime.admin.remove_member(runtime.teacher, runtime.scope)
            else:
                ResultService(runtime.db).patch(
                    runtime.p,
                    batch["batch_id"],
                    saved["result_id"],
                    AnalysisPatch(
                        snapshot_id=batch["snapshot_id"],
                        expected_version=1,
                        note="另一份已提交核验",
                    ),
                )
        with transaction(write=write) as connection:
            yield connection

    monkeypatch.setattr(runtime.db, "transaction", racing)
    response = client.patch(
        base + "/analysis-results/" + saved["result_id"],
        json={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": 1,
            "note": "不应被保存的核验",
        },
    )
    assert mutated and response.status_code == expected, response.text
    with transaction() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM analysis_result_versions WHERE teacher_note=?",
                ("不应被保存的核验",),
            ).fetchone()[0]
            == 0
        )
