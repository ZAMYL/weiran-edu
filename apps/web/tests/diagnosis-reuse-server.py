"""Isolated real Uvicorn server: count calls to the unchanged spawned worker."""

import os
from pathlib import Path

import uvicorn

from app.edu import discovery_worker
from app.main import create_app


def main():
    app = create_app()
    execute = discovery_worker.execute
    counter = Path(os.environ["EDU_WORKER_COUNTER"])

    async def counted(*args, **kwargs):
        with counter.open("a", encoding="utf-8") as stream:
            stream.write("execute\n")
        return await execute(*args, **kwargs)

    discovery_worker.execute = counted
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["EDU_BENCH_PORT"]), access_log=False)


if __name__ == "__main__":
    main()
