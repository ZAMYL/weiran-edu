"""Temporary loopback host for AooMing, reusing the single #72 task contract."""

import argparse
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from prototypes.capability_profiles.synthetic import workbook as profile_workbook
from prototypes.teacher_agent_bridge.fixtures import build_host
from prototypes.teacher_agent_bridge.provider import DatabaseProvider

from app.edu.auth import authenticate
from app.importing.edu import parse_excel

from .api import install
from .demo import QUESTION, server


def bootstrap(client, host, service):
    actor = authenticate(service.store.database, host.token)
    batches = {}
    for case in ("normal", "evidence", "empty", "balanced72", "zero81", "overlap96"):
        response = client.post(
            "/api/v1/edu/imports/preview",
            files={"file": (f"{case}.xlsx", host.books[case], "application/octet-stream")},
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
        if response.status_code != 201:
            code = response.json().get("error", {}).get("code", "UNKNOWN")
            raise RuntimeError(f"synthetic_bootstrap_failed:{case}:{code}")
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
    parser.add_argument(
        "--minimum-cell",
        type=int,
        default=5,
        help="synthetic disclosure threshold (5 by default; 25 for the privacy demo)",
    )
    args = parser.parse_args()
    if not 0 <= args.seconds <= 3600:
        parser.error("invalid_lifetime")
    if not 5 <= args.minimum_cell <= 25:
        parser.error("invalid_minimum_cell")
    # Avoid a shared TEMP override: credentials/DB stay inside the OS user profile.
    private_root = Path(os.environ["LOCALAPPDATA"]) / "weiran-edu-ai-integration"
    private_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="handoff-", dir=private_root) as temporary:
        directory = Path(temporary)
        host = build_host(directory, install_bridge=False)
        # 独立空数据教师：无任何批次、具有导入权，用于验证真实首次使用流程。
        first_use_teacher = host.admin.create_teacher("first-use-owner")
        first_use_scope = host.admin.create_scope("默认来源")
        host.admin.set_member(first_use_teacher, first_use_scope, can_import=True)
        first_use_token = host.admin.issue_token(first_use_teacher, hours=24)
        host.books.update(
            {
                "balanced72": profile_workbook("profile-balanced-72"),
                "zero81": profile_workbook("profile-no-pattern-81"),
                "overlap96": profile_workbook("profile-overlap-96"),
            }
        )
        host.app.state.edu_service.parser = parse_excel
        provider = DatabaseProvider(host.app.state.edu_service, tuple(host.books.values()))
        service = install(host.app, provider, directory / "campaigns")
        service.profile_limits = replace(service.profile_limits, minimum_cell=args.minimum_cell)
        try:
            # Give the operator the exact bytes admitted by this running host.
            first_use_workbook = host.books["normal"]
            first_use_sample = directory / "first-use-synthetic.xlsx"
            first_use_sample.write_bytes(first_use_workbook)
            with (
                server(
                    host, directory, first_use=(first_use_teacher, first_use_workbook)
                ) as origin,
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
                            "minimum_cell": args.minimum_cell,
                            "first_use_token": first_use_token,
                            "first_use_teacher_id": first_use_teacher,
                            "first_use_scope_id": first_use_scope,
                            "first_use_sample_path": str(first_use_sample),
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
                            "first_use_sample_path": str(first_use_sample),
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
            host.admin.revoke_tokens(first_use_teacher)
            print(json.dumps({"stopped": True, "credentials_revoked": True}), flush=True)


if __name__ == "__main__":
    main()
