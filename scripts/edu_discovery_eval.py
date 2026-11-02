"""CLI entrypoint: only built-in fictional cases; no external model calls."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.discovery_v1.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
