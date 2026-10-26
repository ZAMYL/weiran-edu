"""Existing CI collects this prototype gate without changing shared workflows."""

import subprocess
import sys
from pathlib import Path


def test_prototype_ruff_and_format_are_exercised_by_backend_ci():
    root = Path(__file__).resolve().parents[4]
    paths = [
        str(root / "prototypes/adaptive_assessment"),
        str(root / "scripts/edu_adaptive_demo.py"),
        str(root / "scripts/edu_qwen_adaptive_live.py"),
    ]
    for args in (("check",), ("format", "--check")):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                *args,
                "--config",
                str(root / "apps/api/pyproject.toml"),
                *paths,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
