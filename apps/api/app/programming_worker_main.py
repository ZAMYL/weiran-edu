"""Entry point for the formal programming task worker.

Start in a separate process from uvicorn:
    python -m app.programming_worker_main
"""

from __future__ import annotations

import sys

from app.edu.programming_worker import ProgrammingWorker
from app.production import build_programming_worker


def main() -> int:
    try:
        service = build_programming_worker()
    except Exception as error:  # noqa: BLE001 - startup must report the missing gate
        print(f"programming worker refused to start: {error}", file=sys.stderr)
        return 2
    ProgrammingWorker(service).run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
