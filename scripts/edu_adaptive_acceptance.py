"""Repo-scoped offline A/B/C comparison; no paid execution switch."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps/api"))
from evals.adaptive_acceptance.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
