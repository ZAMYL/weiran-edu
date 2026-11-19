"""Run the durable AI worker in its own process.

    python -m app.worker_main

The worker shares SQLite with the HTTP process but owns task execution.
"""

from prototypes.durable_teacher_agent.worker import TaskWorker

from app.production import build_production


def main() -> None:
    _, service = build_production()
    TaskWorker(service).run_forever()


if __name__ == "__main__":
    main()
