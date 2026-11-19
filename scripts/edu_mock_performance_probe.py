"""Cold synthetic TCP/FastAPI/SQLite probe. Prints counters/timings, never rows or tokens."""

import argparse
import cProfile
import json
import socket
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import uvicorn
from app.edu.auth import authenticate
from app.importing.edu import parse_excel
from app.storage.database import Database

from prototypes.adaptive_assessment.agent import AdaptiveExecutor
from prototypes.adaptive_assessment.analysis import Kernel
from prototypes.capability_profiles import agent, engine
from prototypes.capability_profiles.synthetic import workbook
from prototypes.durable_teacher_agent import profiles
from prototypes.durable_teacher_agent import service as task_service
from prototypes.durable_teacher_agent.api import install
from prototypes.durable_teacher_agent.service import TaskService
from prototypes.durable_teacher_agent.store import TaskStore
from prototypes.research_agent_v2.runtime import ResearchExecutor
from prototypes.teacher_agent_bridge.fixtures import build_host
from prototypes.teacher_agent_bridge.provider import DatabaseProvider, DatabaseSource

PHASE = ContextVar("probe_phase", default="setup")


class Probe:
    def __init__(self):
        self.counts = defaultdict(Counter)
        self.seconds = defaultdict(Counter)
        self.top = {}
        self.restore = []

    def wrap(self, owner, name, label, *, phase=None, profile=False, after=None):
        original = getattr(owner, name)
        descriptor = vars(owner).get(name, original)
        self.restore.append((owner, name, descriptor))

        def call(*args, **kwargs):
            token = PHASE.set(phase) if phase else None
            current = PHASE.get()
            started = time.perf_counter()
            self.counts[current][label] += 1
            profiler = cProfile.Profile() if profile else None
            replays = getattr(args[0], "replay_executions", 0) if label == "ledger_verify" else 0
            prior_ledger = args[0]._verified_ledger if label == "ledger_verify" else None
            try:
                result = (
                    profiler.runcall(original, *args, **kwargs)
                    if profiler
                    else original(*args, **kwargs)
                )
                if after:
                    after(self.counts[current], result)
                return result
            finally:
                self.seconds[current][label] += time.perf_counter() - started
                if label == "ledger_verify":
                    self.counts[current]["ledger_query_replays"] += (
                        args[0].replay_executions - replays
                    )
                    if args[0]._verified_ledger is not prior_ledger:
                        self.counts[current]["ledger_replay_passes"] += 1
                if profiler:
                    records = [
                        {
                            "function": entry.code.co_name,
                            "file": Path(entry.code.co_filename).name,
                            "calls": entry.callcount,
                            "seconds": round(entry.totaltime, 4),
                        }
                        for entry in profiler.getstats()
                        if not isinstance(entry.code, str)
                    ]
                    self.top[current] = sorted(records, key=lambda x: -x["seconds"])[:20]
                if token:
                    PHASE.reset(token)

        setattr(owner, name, staticmethod(call) if isinstance(descriptor, staticmethod) else call)

    def install(self, profile, readonly_status=False):
        self.wrap(TaskService, "execute", "task_execute", phase="worker", profile=profile)
        self.wrap(TaskService, "view", "task_view", phase="read", profile=profile)
        self.wrap(TaskService, "verified_result", "verified_result")
        self.wrap(TaskStore, "get", "task_store_get")
        self.wrap(TaskStore, "authorize", "task_authorize")
        self.wrap(TaskService, "replay", "result_replay")
        self.wrap(profiles, "replay_profiles", "profile_replay")
        self.wrap(task_service, "replay_profiles", "profile_replay")
        self.wrap(profiles, "build_profiles", "candidate_search")
        self.wrap(engine.ProfileBook, "verify", "candidate_verify")
        self.wrap(agent, "project_selected", "profile_projection")
        self.wrap(profiles, "project_selected", "profile_projection")
        self.wrap(ResearchExecutor, "_verify", "fact_verify")
        self.wrap(ResearchExecutor, "_verify_ledger", "ledger_verify")
        self.wrap(AdaptiveExecutor, "execute", "kernel_tool_execute")
        self.wrap(AdaptiveExecutor, "derive", "derive")
        self.wrap(Kernel, "__init__", "kernel_build")
        self.wrap(DatabaseSource, "validate", "source_validate")
        self.wrap(
            DatabaseProvider,
            "capture",
            "capture",
            after=lambda c, r: c.update(
                captured_participants=len(r[0].data.participants),
                captured_witnesses=len(r[0].witnesses),
            ),
        )
        self.wrap(
            DatabaseProvider,
            "replay_witnesses",
            "witness_batches",
            after=lambda c, r: c.update(witness_references=len(r)),
        )
        original_connect = Database._connect
        probe = self

        if readonly_status:
            original_get = TaskStore.get
            self.restore.append((TaskStore, "get", original_get))

            def read_status(store, actor, task_id):
                # Diagnostic prototype only, for backend owner review. The original
                # guarded write path still handles the expired-state transition.
                with store.database.transaction() as connection:
                    row = store._row(connection, task_id)
                    store.check(connection, row, actor)
                    expired = (
                        row["state"] in ("queued", "running") and row["deadline"] <= store.clock()
                    )
                return original_get(store, actor, task_id) if expired else row

            TaskStore.get = read_status

        class Connection:
            def __init__(self, connection):
                self.connection = connection
                self.started = time.perf_counter()
                self.phase = PHASE.get()
                self.write = False

            def execute(self, sql, *args, **kwargs):
                if sql.startswith("BEGIN IMMEDIATE"):
                    self.write = True
                    probe.counts[PHASE.get()]["begin_write"] += 1
                probe.counts[PHASE.get()]["sql_" + sql.split()[0].lower()] += 1
                try:
                    return self.connection.execute(sql, *args, **kwargs)
                except sqlite3.Error as exc:
                    probe.counts[PHASE.get()][
                        "sqlite_" + getattr(exc, "sqlite_errorname", "ERROR")
                    ] += 1
                    raise

            def commit(self):
                try:
                    return self.connection.commit()
                except sqlite3.Error as exc:
                    probe.counts[PHASE.get()][
                        "sqlite_"
                        + getattr(exc, "sqlite_errorname", "ERROR")
                        + ("_commit_write" if self.write else "_commit_read")
                    ] += 1
                    raise

            def close(self):
                probe.seconds[self.phase]["connection_hold"] += time.perf_counter() - self.started
                self.connection.close()

            def __getattr__(self, name):
                return getattr(self.connection, name)

        self.restore.append((Database, "_connect", original_connect))
        Database._connect = lambda db: Connection(original_connect(db))

    def close(self):
        for owner, name, original in reversed(self.restore):
            setattr(owner, name, original)

    def report(self):
        return {
            "counts": dict(self.counts),
            "inclusive_seconds": {
                phase: {key: round(value, 4) for key, value in values.items()}
                for phase, values in self.seconds.items()
            },
            "profile_top": self.top,
        }


@contextmanager
def server(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    instance = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, loop="asyncio")
    )
    thread = threading.Thread(target=lambda: instance.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not instance.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("probe_server_unavailable")
            time.sleep(0.05)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        instance.should_exit = True
        thread.join(15)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("probe_server_did_not_stop")


def exercise(case, profile, readers, readonly_status=False, held_reader=False):
    probe = Probe()
    probe.install(profile, readonly_status)
    try:
        with TemporaryDirectory(prefix="edu-mock-perf-") as temp:
            directory = Path(temp)
            host = build_host(directory, install_bridge=False)
            raw = host.books["normal"] if case == "normal" else workbook(case)
            host.app.state.edu_service.parser = parse_excel
            provider = DatabaseProvider(host.app.state.edu_service, (raw,))
            service = install(host.app, provider, directory / "campaigns")
            if held_reader:
                # Isolate SQLite transaction semantics, not an AI execution benchmark.
                # Keep the real API/store/authorization; deliberately leave task queued.
                service.execute = lambda _task_id: None
            actor = authenticate(service.store.database, host.token)
            print(json.dumps({"stage": "host_prepared", "case": case}), flush=True)
            with (
                server(host.app) as origin,
                httpx.Client(
                    base_url=origin,
                    headers={"Authorization": "Bearer " + host.token},
                    timeout=120,
                    trust_env=False,
                ) as client,
            ):
                preview = client.post(
                    "/api/v1/edu/imports/preview",
                    files={"file": ("synthetic.xlsx", raw, "application/octet-stream")},
                )
                preview.raise_for_status()
                value = preview.json()["import"]
                imported = client.post(
                    f"/api/v1/edu/imports/{value['import_id']}/commit",
                    json={
                        "preview_version": value["preview_version"],
                        "acknowledge_warnings": True,
                    },
                )
                imported.raise_for_status()
                batch = imported.json()
                service.store.grant(actor, batch["batch_id"], batch["snapshot_id"])
                print(json.dumps({"stage": "imported_and_granted"}), flush=True)
                base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
                started = time.perf_counter()
                response = client.post(
                    base,
                    json={
                        "snapshot_id": batch["snapshot_id"],
                        "question": "有哪些值得关注的问题？",
                        "idempotency_key": "performance-probe-01",
                    },
                )
                post_seconds = time.perf_counter() - started
                print(
                    json.dumps(
                        {
                            "stage": "post_returned",
                            "http": response.status_code,
                            "seconds": round(post_seconds, 4),
                        }
                    ),
                    flush=True,
                )
                response.raise_for_status()
                path = base + "/" + response.json()["task_id"]
                reads = []
                lock = threading.Lock()

                def poll():
                    completed = 0
                    while time.perf_counter() - started < 240:
                        before = time.perf_counter()
                        try:
                            read = client.get(path, params={"snapshot_id": batch["snapshot_id"]})
                        except httpx.TransportError as error:
                            with lock:
                                reads.append(
                                    {
                                        "http": None,
                                        "seconds": round(time.perf_counter() - before, 4),
                                        "state": None,
                                        "error": type(error).__name__,
                                    }
                                )
                            return
                        body = read.json()
                        row = {
                            "http": read.status_code,
                            "seconds": round(time.perf_counter() - before, 4),
                            "state": body.get("state"),
                            "error": body.get("error_code") or body.get("error", {}).get("code"),
                        }
                        if body.get("result"):
                            result = body["result"]
                            row.update(
                                facts=len(result["facts"]),
                                profiles=len(result["profile_projection"]["profiles"]),
                                model_requests=body["model_requests"],
                                tool_attempts=body["tool_attempts"],
                            )
                        with lock:
                            reads.append(row)
                        if held_reader:
                            return
                        if row["state"] == "completed":
                            completed += 1
                            if completed == 2:
                                return
                        elif row["state"] not in ("queued", "running", None):
                            return
                        time.sleep(0.5)

                transaction = service.store.database.transaction() if held_reader else nullcontext()
                with transaction as connection:
                    if held_reader:
                        connection.execute("SELECT COUNT(*) FROM ai_synthetic_tasks").fetchone()
                    workers = [threading.Thread(target=poll) for _ in range(readers)]
                    for worker in workers:
                        worker.start()
                    for worker in workers:
                        worker.join()
                output = {
                    "case": case,
                    "readers": readers,
                    "backend_readonly_status_prototype": readonly_status,
                    "held_reader_probe": held_reader,
                    "ai_execution": "not_run_lock_probe" if held_reader else "http_mock",
                    "post_seconds": round(post_seconds, 4),
                    "wall_seconds": round(time.perf_counter() - started, 4),
                    "reads": reads,
                    "all_readers_completed_twice": (
                        sum(row["state"] == "completed" for row in reads) == 2 * readers
                        and all(row["http"] == 200 for row in reads)
                    ),
                    "real_qwen": "not_run",
                    **probe.report(),
                }
                host.admin.revoke_tokens(host.teacher_id)
                return output
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="profile-balanced-72")
    parser.add_argument("--readers", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--readonly-status-prototype", action="store_true")
    parser.add_argument("--held-reader-probe", action="store_true")
    args = parser.parse_args()
    result = exercise(
        args.case,
        args.profile,
        args.readers,
        args.readonly_status_prototype,
        args.held_reader_probe,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
