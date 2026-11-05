"""Minimal pytest audit: IDs, outcomes and duration only; no captured payloads."""

import json
import os
from pathlib import Path

nodes = []
reports = []


def pytest_collection_finish(session):
    nodes.extend(item.nodeid for item in session.items)


def pytest_runtest_logreport(report):
    reports.append(
        {
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "duration": report.duration,
            "xfail": bool(getattr(report, "wasxfail", False)),
        }
    )


def pytest_sessionfinish(session, exitstatus):
    path = Path(os.environ["EDU_SHARD_AUDIT"])
    root = Path(__file__).resolve().parents[1] / "artifacts"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("audit_must_stay_in_artifacts")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"nodes": nodes, "reports": reports, "exitstatus": int(exitstatus)}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
