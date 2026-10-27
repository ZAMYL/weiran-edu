"""Focused evidence must retain real tool/source grading and honest billing labels."""

import asyncio

import pytest
from evals.adaptive_acceptance.investigation import build_record, main


def test_focused_investigation_and_zero_finding_control():
    result = asyncio.run(build_record())
    mock = result["offline_http_mock"]
    assert result["fixed_deterministic"]["coverage"] == {"count": 1, "denominator": 1}
    assert mock["evaluation"]["source_accuracy"] == {"count": 4, "denominator": 4}
    assert mock["evaluation"]["passed"]
    assert [t["tool"] for t in mock["audit"]["tool_ledger"]] == [
        "get_dimension_statistics",
        "query_dimension_relation",
        "compare_student_groups",
        "inspect_discovery_support",
    ]
    group = next(c for c in mock["audit"]["cards"] if c["type"] == "item_group_difference")
    assert group["metrics"]["target_count"] == 26
    assert group["metrics"]["remainder_count"] == 27
    assert group["metrics"]["difference"]["numerator"] == 4
    control = result["constant_score_control"]
    assert len(control["audit"]["tool_ledger"]) == 1
    assert control["evaluation"]["zero_finding_correct"]
    assert control["audit"]["cards"] == []
    assert result["paid_api_calls"] == 0
    assert result["ai_incremental_value"] == "not_proven"
    assert result["real_qwen"]["status"] == "not_run"
    assert mock["audit"]["actual_tokens"] is None
    assert mock["audit"]["actual_cost_cny"] is None


def test_focused_cli_refuses_paid_switch_and_overwriting(tmp_path):
    with pytest.raises(SystemExit):
        main(["--execute", "--output", str(tmp_path / "new.json")])
    path = tmp_path / "previous.json"
    path.write_text("previous failure", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["--output", str(path)])
    assert path.read_text(encoding="utf-8") == "previous failure"
