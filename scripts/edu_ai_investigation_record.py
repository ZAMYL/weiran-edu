"""Focused record entry point. Always offline, even when credentials exist."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps/api"))
from evals.adaptive_acceptance.investigation import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
