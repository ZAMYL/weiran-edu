"""Repo-scoped synthetic demo; no teacher data input option."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps/api"))
from prototypes.adaptive_assessment.demo import main  # noqa: E402

if __name__ == "__main__":
    main()
