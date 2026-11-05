"""Temporary loopback host for AooMing, reusing the single #72 task contract."""

import argparse
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from app.edu.auth import authenticate

from prototypes.teacher_agent_bridge.fixtures import build_host
from prototypes.teacher_agent_bridge.provider import DatabaseProvider

from .api import install
from .demo import QUESTION, server


def bootstrap(client, host, service):
    actor = authenticate(service.store.database, host.token)
    batches = {}
    for case in ("normal", "evidence", "empty"):
        response = client.post(
            "/api/v1/edu/imports/preview",
            files={"file": ("synthetic.xlsx", host.books[case], "application/octet-stream")},
        )
        response.raise_for_status()
        preview = response.json()["import"]
        response = client.post(
            f"/api/v1/edu/imports/{preview['import_id']}/commit",
            json={"preview_version": preview["preview_version"], "acknowledge_warnings": True},
        )
        response.raise_for_status()
        ref = response.json()
        service.store.grant(actor, ref["batch_id"], ref["snapshot_id"])
        batches[case] = {
            "batch_id": ref["batch_id"],
            "snapshot_id": ref["snapshot_id"],
            "question": QUESTION
            if case == "normal"
            else (
                "为什么 算法与数据结构 与 代码实现能力 相关"
                if case == "evidence"
                else "识别值得优先核查的评分异常"
            ),
        }
    return batches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=0, help="0: until Ctrl+C; max 3600")
    args = parser.parse_args()
    if not 0 <= args.seconds <= 3600:
        parser.error("invalid_lifetime")
    # OS per-user temp; token/SQLite/journals never enter the checkout or stdout.
    with TemporaryDirectory(prefix="edu-ai-local-handoff-") as temporary:
        directory = Path(temporary)
        host = build_host(directory, install_bridge=False)
        provider = DatabaseProvider(host.app.state.edu_service, tuple(host.books.values()))
        service = install(host.app, provider, directory / "campaigns")
        try:
            with (
                server(host, directory) as origin,
                httpx.Client(
                    base_url=origin,
                    headers={"Authorization": "Bearer " + host.token},
                    timeout=120,
                    trust_env=False,
                ) as client,
            ):
                batches = bootstrap(client, host, service)
                config = directory / "connection.private.json"
                with config.open("x", encoding="utf-8") as target:
                    json.dump(
                        {
                            "api_origin": origin,
                            "bearer_token": host.token,
                            "synthetic_only": True,
                            "execution_origin": "http_mock",
                            "real_qwen": "not_run",
                            "production_enabled": False,
                            "batches": batches,
                        },
                        target,
                        ensure_ascii=False,
                        indent=2,
                    )
                config.chmod(0o600)
                print(
                    json.dumps(
                        {
                            "ready": True,
                            "private_connection_file": str(config),
                            "synthetic_batches": len(batches),
                            "real_qwen": "not_run",
                        }
                    ),
                    flush=True,
                )
                deadline = time.monotonic() + (args.seconds or 3600)
                while time.monotonic() < deadline:
                    time.sleep(0.25)
        except KeyboardInterrupt:
            pass
        finally:
            host.admin.revoke_tokens(host.teacher_id)
            print(json.dumps({"stopped": True, "credentials_revoked": True}), flush=True)


if __name__ == "__main__":
    main()
