"""Single-owner worker for formal programming investigations.

FastAPI only enqueues. This process claims `queued` rows with a conditional
UPDATE, so an HTTP restart cannot silently strand a task and two workers cannot
run the same paid campaign twice.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.edu.programming_tasks import ProgrammingTaskService


class ProgrammingWorker:
    def __init__(
        self,
        service: ProgrammingTaskService,
        *,
        interval: float = 1.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if service.environment != "production":
            raise ValueError("programming_worker_requires_production")
        self.service = service
        self.interval = interval
        self.clock = clock or time.sleep

    def run_once(self, *, limit: int = 1) -> int:
        self.service.expire_stale()
        claimed = 0
        for task_id in self.service.queued(limit=limit):
            self.service.execute(task_id)
            claimed += 1
        return claimed

    def run_forever(self, *, stop: Callable[[], bool] | None = None) -> None:
        while not (stop and stop()):
            if self.run_once() == 0:
                self.clock(self.interval)
