"""Regression: the Windows shard planner assigns every test directory exactly once."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def planner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    return importlib.import_module("plan_windows_pytest_shards")


def _plan(planner, nodes):
    return planner.plan({"source_sha": "a" * 40, "nodes": nodes})


def _assigned(plan):
    return [node for group in plan["groups"].values() for node in group["node_ids"]]


def test_code_directory_is_assigned_to_core_exactly_once(planner):
    nodes = [
        "tests/code/test_engine.py::test_outcome_partitioning",
        "tests/code/test_cpp_features.py::test_recursive_function",
        "tests/analysis/test_correlation.py::test_x",
    ]
    plan = _plan(planner, nodes)
    assert sorted(_assigned(plan)) == sorted(nodes)
    for name, group in plan["groups"].items():
        code_nodes = [n for n in group["node_ids"] if "tests/code/" in n]
        if name == "core":
            assert len(code_nodes) == 2
        else:
            assert code_nodes == []


def test_every_test_directory_is_covered_exactly_once(planner):
    root = Path(__file__).parent
    directories = {file.relative_to(root).parts[0] for file in root.glob("*/**/test_*.py")}
    nodes = [f"tests/{directory}/test_probe.py::test_probe" for directory in sorted(directories)]
    plan = _plan(planner, nodes)
    assert sorted(_assigned(plan)) == sorted(nodes)


def test_unknown_directory_is_still_rejected(planner):
    with pytest.raises(ValueError, match="unknown_or_overlapping_test_directory"):
        _plan(planner, ["tests/not_a_real_dir/test_x.py::test_y"])


def test_overlapping_directory_is_rejected(planner, monkeypatch):
    monkeypatch.setitem(
        planner.GROUPS, "core", {"analysis", "code", "comments", "discovery", "insights"}
    )
    monkeypatch.setitem(
        planner.GROUPS,
        "ai_research",
        {"adaptive_assessment", "ai_edu", "research_v2", "research_v22", "code"},
    )
    with pytest.raises(ValueError, match="unknown_or_overlapping_test_directory"):
        _plan(planner, ["tests/code/test_x.py::test_y"])
