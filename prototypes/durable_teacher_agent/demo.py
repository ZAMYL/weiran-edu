"""Actual loopback HTTP, separate Uvicorn processes, same temporary SQLite + journals."""

import argparse
import json
import multiprocessing
import socket
import threading
import time
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import uvicorn
from app.core.config import Settings
from app.edu.auth import authenticate
from app.main import create_app
from openpyxl import load_workbook

from prototypes.teacher_agent_bridge.fixtures import build_host
from prototypes.teacher_agent_bridge.provider import DatabaseProvider

from .api import install
from .first_use import FirstUseImportService

QUESTION = "比较 算法与数据结构、代码实现能力 的组合与 问题建模能力 的群体差异"


def serve(database_path, directory, books, connection, stop, first_use=None):
    app = create_app(Settings(environment="test", database_path=Path(database_path)))
    provider = DatabaseProvider(app.state.edu_service, tuple(books.values()))
    service = install(app, provider, Path(directory))
    if first_use is not None:
        app.state.edu_service = FirstUseImportService(
            app.state.edu_service, service.store, *first_use
        )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))

    def shutdown():
        stop.wait()
        server.should_exit = True

    threading.Thread(target=shutdown, daemon=True).start()
    connection.send(sock.getsockname()[1])
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
        connection.close()


@contextmanager
def server(fixture, directory, *, first_use: tuple[str, bytes] | None = None):
    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    stop = ctx.Event()
    process = ctx.Process(
        target=serve,
        args=(
            str(fixture.app.state.edu_service.database.path),
            str(directory / "campaigns"),
            fixture.books,
            send,
            stop,
            first_use,
        ),
    )
    process.start()
    send.close()
    try:
        if not receive.poll(30):
            raise RuntimeError("synthetic_server_start_timeout")
        port = receive.recv()
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while True:
            try:
                response = httpx.get(base + "/api/v1/health", timeout=2, trust_env=False)
                if response.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("synthetic_server_unavailable")
            time.sleep(0.1)
        yield base
    finally:
        stop.set()
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join(10)
        receive.close()


def exercise(directory):
    fixture = build_host(directory, install_bridge=False)
    provider = DatabaseProvider(fixture.app.state.edu_service, tuple(fixture.books.values()))
    local = install(fixture.app, provider, directory / "campaigns")
    actor = authenticate(local.store.database, fixture.token)
    checks, runs = [], {}

    def request(client, method, path, expected=200, **kwargs):
        response = client.request(method, path, **kwargs)
        checks.append(
            {"method": method, "path": path, "expected": expected, "status": response.status_code}
        )
        if response.status_code != expected:
            raise RuntimeError(
                f"synthetic_http_check_failed:{len(checks)}:{response.status_code}:{response.text[:150]}"
            )
        return response

    def upload(client, case):
        preview = request(
            client,
            "POST",
            "/api/v1/edu/imports/preview",
            201,
            files={
                "file": (
                    "synthetic.xlsx",
                    fixture.books[case],
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        ).json()["import"]
        return request(
            client,
            "POST",
            f"/api/v1/edu/imports/{preview['import_id']}/commit",
            201,
            json={"preview_version": preview["preview_version"], "acknowledge_warnings": True},
        ).json()

    def wait(client, path, snapshot):
        deadline = time.monotonic() + 140
        while True:
            response = request(client, "GET", path, params={"snapshot_id": snapshot}).json()
            if response["state"] not in ("queued", "running"):
                if response["state"] != "completed":
                    raise RuntimeError(
                        "synthetic_investigation_failed:" + str(response["error_code"])
                    )
                return response
            if time.monotonic() > deadline:
                raise RuntimeError("synthetic_task_poll_timeout")
            time.sleep(1)

    try:
        with (
            server(fixture, directory) as base,
            httpx.Client(
                base_url=base,
                headers={"Authorization": "Bearer " + fixture.token},
                timeout=120,
                trust_env=False,
            ) as client,
        ):
            for case, question in (
                ("normal", QUESTION),
                ("evidence", "为什么 算法与数据结构 与 代码实现能力 相关"),
                ("empty", "识别值得优先核查的评分异常"),
            ):
                batch = upload(client, case)
                bid, snapshot = batch["batch_id"], batch["snapshot_id"]
                grant = local.store.grant(actor, bid, snapshot)
                path = f"/api/v1/edu/batches/{bid}/ai-investigation-tasks"
                body = {
                    "snapshot_id": snapshot,
                    "question": question,
                    "idempotency_key": "demo-task-" + case,
                }
                response = request(client, "POST", path, 202, json=body).json()
                task_path = path + "/" + response["task_id"]
                view = wait(client, task_path, snapshot)
                request(client, "POST", path, 202, json=body)
                request(
                    client, "POST", path, 409, json=body | {"idempotency_key": "new-id-" + case}
                )
                runs[case] = {
                    "batch": batch,
                    "grant_id": grant,
                    "path": task_path,
                    "body": body,
                    "view": view,
                }
        # The first server process has exited. A different process reconstructs the
        # full service, provider, task and source proofs from the same persisted state.
        with (
            server(fixture, directory) as base,
            httpx.Client(
                base_url=base,
                headers={"Authorization": "Bearer " + fixture.token},
                timeout=120,
                trust_env=False,
            ) as client,
        ):
            normal = runs["normal"]
            b, path, before = normal["batch"], normal["path"], normal["view"]
            snapshot = b["snapshot_id"]
            reopened = request(client, "GET", path, params={"snapshot_id": snapshot}).json()
            assert reopened == before
            facts = reopened["result"]["facts"]
            group = next(f for f in facts if f["method"] == "compare_student_groups")
            relation = next(f for f in facts if f["method"] == "query_dimension_relation")
            source = next(s for s in group["source_refs"] if s["metric_key"] == "target.valid")
            records = request(
                client, "POST", source["records_path"], json=source["records_query"] | {"limit": 2}
            ).json()
            assert records["total_count"] == 26
            for link in records["items"]:
                evidence = request(
                    client, "GET", link["evidence_path"], params={"snapshot_id": snapshot}
                ).json()
                assert evidence["score"] == 1 and evidence["comment"] is None
            receipt = request(
                client,
                "POST",
                path + "/review-submissions",
                json={"snapshot_id": snapshot, "fact_ids": [relation["fact_id"], group["fact_id"]]},
            ).json()
            pending = request(
                client, "GET", receipt["result_path"], params={"snapshot_id": snapshot}
            ).json()
            assert all(
                f["teacher_review"]["status"] == "pending" for f in pending["discovery_findings"]
            )
            confirmed = request(
                client,
                "PATCH",
                receipt["review_path"],
                json={
                    "snapshot_id": snapshot,
                    "expected_version": 1,
                    "decisions": [
                        {"candidate_id": cid, "status": "confirmed"}
                        for cid in receipt["candidate_ids"]
                    ],
                },
            ).json()
            assert confirmed["version"] == 2
            for fmt in ("md", "xlsx"):
                report = request(
                    client,
                    "GET",
                    receipt["export_path"],
                    params={"snapshot_id": snapshot, "expected_version": 2, "format": fmt},
                )
                if fmt == "xlsx":
                    book = load_workbook(BytesIO(report.content))
                    assert len(book.sheetnames) >= 4
                    book.close()
                else:
                    assert "53" in report.text and "相关" in report.text
            request(
                client,
                "GET",
                receipt["export_path"],
                409,
                params={"snapshot_id": snapshot, "expected_version": 1, "format": "md"},
            )
            local.store.revoke(normal["grant_id"])
            request(client, "GET", path, 403, params={"snapshot_id": snapshot})
            fixture.admin.revoke_tokens(fixture.teacher_id)
            request(
                client,
                "GET",
                runs["evidence"]["path"],
                401,
                params={"snapshot_id": runs["evidence"]["batch"]["snapshot_id"]},
            )
        assert runs["empty"]["view"]["result"]["selected_fact_ids"] == []
        assert runs["evidence"]["view"]["result"]["stop_reason"] == "needs_external_evidence"
        # Do not publish participant links, workbook bytes, credentials or private DB.
        return {
            "origin": "http_mock",
            "real_qwen": "not_run",
            "actual_remote_requests": 0,
            "process_restart_verified": True,
            "checks": checks,
            "runs": {case: run["view"] for case, run in runs.items()},
            "review_receipt": receipt,
            "report_version": 2,
        }
    finally:
        fixture.admin.revoke_tokens(fixture.teacher_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_files = sorted(Path(__file__).parent.glob("*.py")) + [
        Path("prototypes/teacher_agent_bridge/api.py"),
        Path("prototypes/teacher_agent_bridge/models.py"),
        Path("prototypes/teacher_agent_bridge/fixtures.py"),
        Path("prototypes/research_agent_v2/runtime.py"),
    ]

    def fingerprint():
        return sha256(b"".join(str(p).encode() + p.read_bytes() for p in source_files)).hexdigest()

    before = fingerprint()
    with TemporaryDirectory(prefix="edu-durable-synthetic-") as directory:
        result = exercise(Path(directory))
    if fingerprint() != before:
        raise RuntimeError("source_changed_during_synthetic_validation")
    result["implementation_sha256"] = before
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "checks": len(result["checks"]),
                "process_restart_verified": True,
                "real_qwen": "not_run",
            }
        )
    )


if __name__ == "__main__":
    main()
