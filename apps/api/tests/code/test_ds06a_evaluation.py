"""Regression tests for the independent DS-06A evaluation harness."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from evaluation.ds06a.baseline import run_baseline
from evaluation.ds06a.c01 import run_c01
from evaluation.ds06a.metrics import score_result
from evaluation.ds06a.model import Case, load_cases, load_oracle
from evaluation.ds06a.pipelines import run_ds03
from evaluation.ds06a.run_eval import run_benchmark, run_once, write_outputs

DATA_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "ds06a" / "data"


def case_by_id(case_id: str) -> Case:
    return load_cases(DATA_DIR, case_id)[0]


def test_synthetic_cases_and_human_oracles_are_separate_and_complete() -> None:
    cases = load_cases(DATA_DIR)
    assert len(cases) == 11
    assert all(load_oracle(DATA_DIR, case)["human_review"]["rationale"] for case in cases)
    for path in (DATA_DIR / "cases").glob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        assert '"expected' not in text
        assert '"oracle' not in text
        assert '"root_cause' not in text


def test_all_three_real_pipelines_meet_the_independent_standard() -> None:
    rows = run_once(DATA_DIR)
    assert len(rows) == 33
    for row in rows:
        metrics = row["metrics"]
        assert metrics["rejection_correct"]
        assert metrics["statistics_correct"] is not False
        assert metrics["phenomenon_accuracy"] in {None, 1.0}
        assert metrics["unsupported_root_assertions"] == 0
        for name in ("evidence", "feature", "association", "abstention", "source"):
            assert (metrics[name + "_success"] or 0) == metrics[name + "_total"], (
                row["case_id"],
                row["system"],
                name,
                metrics,
            )
    summary = run_benchmark(DATA_DIR)["summary"]
    assert summary["A"]["feature_extraction_accuracy"] is None
    assert summary["A"]["association_accuracy"] is None
    assert summary["B"]["feature_extraction_accuracy"] == 1.0
    assert summary["C"]["association_accuracy"] == 1.0


def test_repeated_runs_are_semantically_deterministic(tmp_path: Path) -> None:
    report = run_benchmark(DATA_DIR, repeat=2)
    assert report["semantic_repeat_deterministic"] is True
    assert report["real_qwen_called"] is False
    assert report["real_token_usage"] is None
    assert report["real_model_cost"] is None
    write_outputs(report, tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == {
        "metrics.json",
        "summary.csv",
        "case_results.csv",
        "report.md",
    }


def test_ds03_adapter_calls_the_real_analyze_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    import evaluation.ds06a.pipelines as pipelines

    real_analyze = pipelines.ds03_analyze
    calls = []

    def recording_analyze(*args, **kwargs):
        calls.append((args, kwargs))
        return real_analyze(*args, **kwargs)

    monkeypatch.setattr(pipelines, "ds03_analyze", recording_analyze)
    result = run_ds03(case_by_id("varied_distribution"))
    assert result["status"] == "ok"
    assert len(calls) == 2
    assert calls[0][0][2][0].__class__.__name__ == "SubmissionRecord"
    assert result["candidates"]


def test_c01_adapter_calls_the_real_investigation(monkeypatch: pytest.MonkeyPatch) -> None:
    import evaluation.ds06a.c01 as c01

    real_investigate = c01.investigate_programming
    calls = []

    async def recording_investigate(*args, **kwargs):
        calls.append((args, kwargs))
        return await real_investigate(*args, **kwargs)

    monkeypatch.setattr(c01, "investigate_programming", recording_investigate)
    result = run_c01(case_by_id("association_not_causation"))
    assert result["status"] == "ok"
    assert len(calls) == 1
    assert calls[0][1]["transport"] is not None
    assert result["cards"][0]["limitations"][-1] == "cause_unconfirmed"
    assert result["source_verified"] is True


def test_negative_control_catches_extra_phenomenon_and_bad_source() -> None:
    case = case_by_id("same_verdict_different_features")
    oracle = load_oracle(DATA_DIR, case)
    result = deepcopy(run_baseline(case))
    result["phenomena"][0]["affected"][0]["locator"] = "tampered/location"
    extra = deepcopy(result["phenomena"][0])
    extra["status"] = "runtime_error"
    result["phenomena"].append(extra)
    metrics = score_result(case, oracle, result)
    assert metrics["phenomenon_accuracy"] < 1.0
    assert metrics["evidence_success"] < metrics["evidence_total"]
    assert metrics["source_success"] < metrics["source_total"]


def test_structural_and_source_integrity_rejections_are_not_hidden() -> None:
    duplicate = case_by_id("duplicate_submission")
    assert run_baseline(duplicate)["error"] == "duplicate_submission"
    assert run_ds03(duplicate)["error"] == "duplicate_submission"
    assert run_c01(duplicate)["error"] == "duplicate_submission"
    missing = case_by_id("missing_witness")
    assert run_baseline(missing)["status"] == "ok"
    assert run_ds03(missing)["status"] == "ok"
    assert run_c01(missing)["error"] == "source_scope_mismatch"
    mismatch = case_by_id("witness_mismatch")
    assert run_baseline(mismatch)["status"] == "ok"
    assert run_ds03(mismatch)["status"] == "ok"
    assert run_c01(mismatch)["error"] == "source_scope_mismatch"
