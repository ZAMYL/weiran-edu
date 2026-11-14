"""Test-only phase tracing; imports app/prototypes from the selected immutable worktree."""

import argparse
import asyncio
import contextvars
import faulthandler
import json
import os
import socket
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

request_id = contextvars.ContextVar("trace_request", default="-")
worker_active = contextvars.ContextVar("trace_worker", default=False)
write_lock = threading.Lock()


def event(stage: str, **fields) -> None:
    target = os.environ.get("EDU_BLOCKER_TRACE")
    if target:
        row = {
            "ms": round(time.perf_counter() * 1000, 3),
            "epoch_ms": round(time.time() * 1000),
            "pid": os.getpid(),
            "thread": threading.get_ident(),
            "rid": request_id.get(),
            "stage": stage,
            **fields,
        }
        with write_lock, open(target, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")


class Sender:
    def __init__(self, original):
        self.original = original

    def send(self, value):
        event("child_send_begin", kind=value[0], findings=len(getattr(value[1], "findings", ())))
        self.original.send(value)
        event("child_send_end")

    def close(self):
        self.original.close()


def traced_compute(*args) -> None:
    event("child_target_enter")
    from app.edu.discovery_worker import compute_child

    event("child_worker_imported")
    values = list(args)
    values[-1] = Sender(values[-1])
    try:
        compute_child(*values)
    finally:
        event("child_target_exit")


class TracedASGI:
    def __init__(self, app, heartbeat=None):
        self.app = app
        self.heartbeat = heartbeat

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            pulse_task = None

            async def lifespan_send(message):
                nonlocal pulse_task
                if message["type"] == "lifespan.startup.complete" and self.heartbeat:
                    pulse_task = asyncio.create_task(self.heartbeat())
                if message["type"] == "lifespan.shutdown.complete" and pulse_task:
                    pulse_task.cancel()
                await send(message)

            return await self.app(scope, receive, lifespan_send)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = request_id.set(os.urandom(5).hex())
        event("http_enter", path=scope["path"], method=scope["method"])

        async def observed(message):
            if message["type"] == "http.response.start":
                event("http_status", status=message["status"])
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                event("http_body_done")
            await send(message)

        try:
            await self.app(scope, receive, observed)
        finally:
            event("http_exit")
            request_id.reset(token)


def instrument() -> None:
    from multiprocessing.process import BaseProcess

    from app.edu import discovery_worker as worker
    from app.edu.discovery_bridge import DiscoveryInputReader
    from app.storage.database import Database

    from prototypes.durable_teacher_agent.store import TaskStore

    for name in ("start", "join", "terminate", "kill"):
        original = getattr(BaseProcess, name)

        def wrap(method, label):
            @wraps(method)
            def call(self, *args, **kwargs):
                if worker_active.get():
                    event("process_" + label + "_begin", child=self.pid)
                value = method(self, *args, **kwargs)
                if worker_active.get():
                    event("process_" + label + "_end", child=self.pid)
                return value

            return call

        setattr(BaseProcess, name, wrap(original, name))

    execute = worker.execute

    async def execution(*args, **kwargs):
        marker = worker_active.set(True)
        event("worker_execute_begin", students=len(args[3]), timeout=kwargs.get("timeout"))
        try:
            value = await execute(*args, **kwargs)
            event("worker_execute_ok", findings=len(value.findings))
            return value
        except BaseException as error:
            event(
                "worker_execute_error",
                error=type(error).__name__,
                code=getattr(error, "code", None),
            )
            raise
        finally:
            event("worker_execute_exit")
            worker_active.reset(marker)

    worker.execute = execution
    worker.compute_child = traced_compute
    receive = worker._receive

    def reception(receiver):
        event("parent_receive_begin")
        try:
            value = receive(receiver)
            event("parent_receive_ok")
            return value
        finally:
            event("parent_receive_exit")

    worker._receive = reception
    transaction = Database.transaction

    @contextmanager
    def database(self, *, write=False):
        active = request_id.get() != "-"
        if active:
            event("db_begin", write=write)
        with transaction(self, write=write) as connection:
            if active:
                event("db_acquired", write=write)
            yield connection
        if active:
            event("db_closed", write=write)

    Database.transaction = database
    loading = DiscoveryInputReader._load_input

    def load(self, *args, **kwargs):
        event("input_load_begin")
        value = loading(self, *args, **kwargs)
        event("input_load_end", students=len(value.student_ids))
        return value

    DiscoveryInputReader._load_input = load
    creating = TaskStore.create

    def create(self, *args, **kwargs):
        event("ai_create_begin")
        try:
            return creating(self, *args, **kwargs)
        finally:
            event("ai_create_exit")

    TaskStore.create = create


def make_data(directory: Path) -> None:
    import importlib.util

    from prototypes.capability_profiles.synthetic import workbook
    from prototypes.teacher_agent_bridge.fixtures import workbook as normal

    directory.mkdir(parents=True, exist_ok=True)
    for key, raw in {
        "72": workbook("profile-balanced-72"),
        "96": workbook("profile-overlap-96"),
        "53": normal("normal"),
    }.items():
        (directory / (key + ".xlsx")).write_bytes(raw)
    module_path = Path(os.environ["EDU_TRACE_SOURCE"]) / "apps/web/tests/make-discovery-fixtures.py"
    spec = importlib.util.spec_from_file_location("synthetic_discovery_fixture", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.make(directory, "precision105")
    (directory / "105.xlsx").write_bytes((directory / "precision105.xlsx").read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--case", choices=("72", "53", "96", "105"))
    parser.add_argument("--make-data", type=Path)
    parser.add_argument(
        "--mode", default="stdin_thread", choices=("stdin_thread", "poll_thread", "main_loop")
    )
    args = parser.parse_args()
    if args.make_data:
        make_data(args.make_data)
        return
    import httpx
    import uvicorn
    from app.edu.auth import authenticate
    from app.importing.edu import parse_excel

    from prototypes.durable_teacher_agent.api import install
    from prototypes.teacher_agent_bridge.fixtures import build_host
    from prototypes.teacher_agent_bridge.provider import DatabaseProvider

    args.directory.mkdir(parents=True, exist_ok=True)
    os.environ["EDU_BLOCKER_TRACE"] = str(args.directory / "phases.jsonl")
    trace_file = (args.directory / "stacks.txt").open("w", encoding="utf-8")
    faulthandler.enable(trace_file)
    instrument()
    host = build_host(args.directory, install_bridge=False)
    host.app.state.edu_service.parser = parse_excel
    keys = (args.case,) if args.case else ("72", "53", "96", "105")
    host.books = {key: (args.data / (key + ".xlsx")).read_bytes() for key in keys}
    service = install(
        host.app,
        DatabaseProvider(host.app.state.edu_service, tuple(host.books.values())),
        args.directory / "campaigns",
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"

    heartbeat = [time.perf_counter()]
    stop_watchdog = threading.Event()

    async def pulse():
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(0.25)
            current = time.perf_counter()
            heartbeat[0] = current
            if current - previous > 1:
                event("event_loop_gap", gap_ms=round((current - previous) * 1000))
            previous = current

    api = uvicorn.Server(
        uvicorn.Config(TracedASGI(host.app, pulse), log_level="critical", access_log=False)
    )

    def bootstrap_and_control():
        deadline = time.monotonic() + 30
        while not api.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn_start_timeout")
            time.sleep(0.05)
        batches = {}
        with httpx.Client(
            base_url=origin,
            headers={"Authorization": "Bearer " + host.token},
            timeout=120,
            trust_env=False,
        ) as client:
            for key, raw in host.books.items():
                scope = host.admin.create_scope("合成严格对照-" + key)
                host.admin.set_member(host.teacher_id, scope, can_import=True)
                response = client.post(
                    "/api/v1/edu/imports/preview",
                    files={"file": ("synthetic.xlsx", raw, "application/octet-stream")},
                    data={"title": "合成严格对照-" + key, "data_scope_id": scope},
                )
                response.raise_for_status()
                preview = response.json()["import"]
                response = client.post(
                    f"/api/v1/edu/imports/{preview['import_id']}/commit",
                    json={
                        "preview_version": preview["preview_version"],
                        "acknowledge_warnings": True,
                    },
                )
                event(
                    "seed_commit",
                    dataset=key,
                    status=response.status_code,
                    code=response.json().get("error", {}).get("code"),
                )
                response.raise_for_status()
                batch = response.json()
                if key != "105":
                    service.store.grant(
                        authenticate(service.store.database, host.token),
                        batch["batch_id"],
                        batch["snapshot_id"],
                    )
                batches[key] = {"batch_id": batch["batch_id"], "snapshot_id": batch["snapshot_id"]}
        private = args.directory / "connection.private.json"
        private.write_text(
            json.dumps(
                {
                    "api_origin": origin,
                    "bearer_token": host.token,
                    "batches": batches,
                    "synthetic_only": True,
                }
            ),
            encoding="utf-8",
        )
        event("ready", mode=args.mode)

        def watchdog():
            while not stop_watchdog.wait(5):
                gap = time.perf_counter() - heartbeat[0]
                if gap > 5:
                    event("watchdog_loop_stalled", gap_ms=round(gap * 1000))
                    trace_file.write(f"\nEvent loop stalled {gap:.3f}s\n")
                    for identity, frame in sys._current_frames().items():
                        trace_file.write(
                            f"Thread {identity}\n" + "".join(traceback.format_stack(frame))
                        )
                    trace_file.flush()

        threading.Thread(target=watchdog, daemon=True).start()
        print(json.dumps({"ready": True, "connection": str(private)}), flush=True)
        if args.mode == "stdin_thread":
            sys.stdin.readline()
        else:
            while not (args.directory / "stop.flag").exists():
                time.sleep(0.1)
        event("control_stop")
        api.should_exit = True

    try:
        if args.mode == "main_loop":
            control = threading.Thread(target=bootstrap_and_control, daemon=True)
            control.start()
            api.run(sockets=[sock])
        else:
            thread = threading.Thread(target=api.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            bootstrap_and_control()
            thread.join(5)
    finally:
        stop_watchdog.set()
        faulthandler.cancel_dump_traceback_later()
        api.should_exit = True
        sock.close()
        event("host_exit")


if __name__ == "__main__":
    main()
