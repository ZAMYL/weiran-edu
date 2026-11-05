"""Audit-only fixtures, not a substitute for actual Windows execution artifacts."""

import copy
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("windows_shards")


def records(audit, nodes):
    revision = "a" * 40
    groups = audit.plan({"source_sha": revision, "nodes": nodes})["groups"]
    result = []
    for name, group in groups.items():
        expected = group["node_ids"]
        result.append(
            {
                "source_sha": revision,
                "test_baseline_sha": revision,
                "shard": name,
                "suite": nodes,
                "expected": expected,
                "pytest_exit_code": 0 if expected else None,
                "execution_status": "executed" if expected else "not_run_empty_shard",
                "elapsed_seconds": 1,
                "execution": {
                    "nodes": expected,
                    "exitstatus": 0,
                    "reports": [
                        {"nodeid": n, "when": phase, "outcome": "passed", "xfail": False}
                        for n in expected
                        for phase in ("setup", "call", "teardown")
                    ],
                }
                if expected
                else None,
            }
        )
    return result


def write(folder, items):
    for index, item in enumerate(items):
        path = folder / str(index)
        path.mkdir()
        (path / "result.json").write_text(json.dumps(item), encoding="utf-8")


def test_empty_groups_are_not_faked_as_executed(audit, tmp_path):
    items = records(audit, ["tests/test_example.py::test_one"])
    write(tmp_path, items)
    result = audit.verify(tmp_path, "a" * 40)
    assert result["total_passed"] == 1
    assert result["missing"] == result["duplicates"] == 0
    assert sum(s.get("status") == "not_run_empty_shard" for s in result["shards"]) == 4


@pytest.mark.parametrize(
    "failure",
    [
        "skip",
        "wrong_sha",
        "duplicate_phase",
        "missing_shard",
        "unknown_node",
        "empty_fake_execution",
    ],
)
def test_audit_rejects_incomplete_or_untrusted_outcomes(audit, tmp_path, failure):
    items = copy.deepcopy(records(audit, ["tests/test_example.py::test_one"]))
    if failure == "skip":
        items[0]["execution"]["reports"][0]["outcome"] = "skipped"
    elif failure == "wrong_sha":
        items[0]["source_sha"] = "b" * 40
    elif failure == "duplicate_phase":
        items[0]["execution"]["reports"].append(items[0]["execution"]["reports"][0])
    elif failure == "missing_shard":
        items.pop()
    elif failure == "unknown_node":
        items[0]["execution"]["nodes"] = ["tests/test_example.py::invented"]
    else:
        items[1]["execution"] = {"nodes": [], "reports": [], "exitstatus": 0}
    write(tmp_path, items)
    with pytest.raises(ValueError):
        audit.verify(tmp_path, "a" * 40)
