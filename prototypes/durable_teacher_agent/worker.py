"""Single-writer durable task worker. The HTTP process never owns execution."""

import time
from threading import Event

from .service import TaskService


class TaskWorker:
    """Claim queued tasks one at a time; a second worker loses the atomic claim."""

    def __init__(self, service: TaskService, *, interval: float = 1.0):
        if not 0.1 <= interval <= 30:
            raise ValueError("invalid_worker_interval")
        self.service = service
        self.interval = interval

    def run_once(self, *, limit: int = 1) -> int:
        claimed = self.service.store.queued(limit=limit)
        for task_id in claimed:
            self.service.execute(task_id)
        return len(claimed)

    def run_forever(self, stop: Event | None = None) -> None:
        while stop is None or not stop.is_set():
            if self.run_once() == 0:
                if stop is not None:
                    stop.wait(self.interval)
                else:
                    time.sleep(self.interval)
