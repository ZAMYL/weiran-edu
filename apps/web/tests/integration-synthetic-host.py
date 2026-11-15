"""Disposable registered-synthetic host; a private stop file ends only its own server."""

import json
import socket
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> None:
    # Spawned discovery children must not import setup-only AI modules via __mp_main__.
    import httpx
    import uvicorn
    from app.importing.edu import parse_excel

    from prototypes.capability_profiles.synthetic import workbook
    from prototypes.durable_teacher_agent.api import install
    from prototypes.durable_teacher_agent.serve import bootstrap
    from prototypes.teacher_agent_bridge.fixtures import build_host
    from prototypes.teacher_agent_bridge.provider import DatabaseProvider

    with TemporaryDirectory(prefix="edu-analysis-perf-integration-") as temporary:
        directory = Path(temporary)
        host = build_host(directory, install_bridge=False)
        host.books.update(
            {
                "balanced72": workbook("profile-balanced-72"),
                "zero81": workbook("profile-no-pattern-81"),
                "overlap96": workbook("profile-overlap-96"),
            }
        )
        host.app.state.edu_service.parser = parse_excel
        provider = DatabaseProvider(
            host.app.state.edu_service, tuple(host.books.values())
        )
        service = install(host.app, provider, directory / "campaigns")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        api = uvicorn.Server(
            uvicorn.Config(host.app, log_level="critical", access_log=False)
        )
        thread = threading.Thread(
            target=api.run, kwargs={"sockets": [sock]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not api.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("synthetic_uvicorn_start_timeout")
                time.sleep(0.05)
            with httpx.Client(
                base_url=origin,
                headers={"Authorization": "Bearer " + host.token},
                timeout=120,
                trust_env=False,
            ) as client:
                batches = bootstrap(client, host, service)
                connection = directory / "connection.private.json"
                connection.write_text(
                    json.dumps(
                        {
                            "api_origin": origin,
                            "bearer_token": host.token,
                            "synthetic_only": True,
                            "execution_origin": "http_mock",
                            "real_qwen": "not_run",
                            "production_enabled": False,
                            "batches": batches,
                        }
                    ),
                    encoding="utf-8",
                )
                print(
                    json.dumps({"ready": True, "connection": str(connection)}),
                    flush=True,
                )
                # A blocking stdin read can stall Windows spawn while its parent sends
                # the discovery input, freezing the API event loop. Keep stdin idle.
                while not (directory / "stop.flag").exists():
                    time.sleep(0.1)
        finally:
            api.should_exit = True
            thread.join(30)
            sock.close()
            host.admin.revoke_tokens(host.teacher_id)


if __name__ == "__main__":
    main()
