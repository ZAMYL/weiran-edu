"""Run with apps/api's locked uv environment. Default is dry-run."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps/api"))
from prototypes.adaptive_assessment.live.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
