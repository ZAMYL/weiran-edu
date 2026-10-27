"""Prepare a new disposable V1 demo using existing admin CLI and synthetic fixtures only."""

import argparse
import json
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps/api"


def prepare(destination: Path | None = None) -> dict:
    temporary = Path(tempfile.gettempdir()).resolve()
    if destination is None:
        runtime = Path(tempfile.mkdtemp(prefix="weiran-v1-demo-"))
    else:
        if (
            not destination.is_absolute()
            or destination.exists()
            or destination.parent.resolve() != temporary
        ):
            raise ValueError("NEW_DIRECT_SYSTEM_TEMP_DIRECTORY_REQUIRED")
        runtime = destination.resolve()
        runtime.mkdir()
    database = runtime / "synthetic.sqlite3"
    env = {**os.environ, "PYTHONPATH": str(API), "PYTHONUTF8": "1", "WEIRAN_EDU_ENV": "test"}

    def admin(*arguments):
        value = subprocess.run(
            [sys.executable, "-m", "app.edu", "--database", str(database), *arguments],
            cwd=API,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        if value.returncode:
            raise ValueError("SYNTHETIC_ADMIN_SETUP_FAILED")
        return json.loads(value.stdout)

    admin("init")
    owner = admin("teacher-create", "--subject", "v1-synthetic-demo-only")["teacher_id"]
    scope = admin("scope-create", "--label", "V1纯合成验收来源")["data_scope_id"]
    admin("member-set", "--teacher-id", owner, "--scope-id", scope, "--can-import")
    credential = admin("token-issue", "--teacher-id", owner, "--hours", "24")["access_token"]
    private = runtime / "server-session.private.json"
    with private.open("x", encoding="utf-8") as stream:
        json.dump({"synthetic_only": True, "access_token": credential}, stream)
    sys.path.insert(0, str(API))
    factory = runpy.run_path(str(ROOT / "apps/web/tests/make-discovery-fixtures.py"))["make"]
    factory(runtime, "contrast")
    factory(runtime, "constant")
    configuration = {
        "synthetic_only": True,
        "database": str(database),
        "session_file": str(private),
        "teacher_id": owner,
        "fixtures": {
            "contrast53": str(runtime / "contrast.xlsx"),
            "constant17": str(runtime / "constant.xlsx"),
        },
    }
    config = runtime / "runtime.json"
    with config.open("x", encoding="utf-8") as stream:
        json.dump(configuration, stream, ensure_ascii=False, indent=2)
    return {
        "status": "prepared",
        "runtime_dir": str(runtime),
        "config_file": str(config),
        "students": [53, 17],
        "credentials_printed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.runtime_dir), ensure_ascii=False))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError):
        print(json.dumps({"status": "failed", "code": "DEMO_PREPARATION_FAILED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
