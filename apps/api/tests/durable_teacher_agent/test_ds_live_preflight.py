import json
from datetime import date
from hashlib import sha256

import httpx
from prototypes.adaptive_assessment.live.pricing import (
    REVIEWED_PRICE_20261112,
)
from prototypes.adaptive_assessment.live.pricing import (
    REVIEWED_PRICE_20261125 as PRICE,
)
from prototypes.qwen_execution import code_fingerprint
from scripts.edu_qwen_live_preflight import inspect_environment

TODAY = date(2026, 11, 26)
ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def configured(tmp_path):
    endpoint_sha = sha256(ENDPOINT.encode()).hexdigest()
    code = code_fingerprint()
    source = "a" * 64
    live = {
        "campaign_id": "fixture-campaign-123",
        "purpose": "database_live",
        "human_approval_reference": "fixture-owner-123",
        "independent_review_reference": "fixture-review-123",
        "reviewed_code_sha256": code,
        "valid_until": "2026-12-02",
        "model": "qwen3.8-flash",
        "endpoint_sha256": endpoint_sha,
        "source_fingerprints": {source: source},
        "price_fingerprint": PRICE.fingerprint,
        "max_requests": 1,
        "max_output_tokens": 800,
        "total_token_limit": 992608,
        "cost_cap_cny": "0.80",
    }
    real = {
        "purpose": "real_teacher_data",
        "human_approval_reference": "fixture-owner-456",
        "independent_review_reference": "fixture-review-456",
        "reviewed_code_sha256": code,
        "valid_until": "2026-12-02",
        "sources": [
            {
                "source_sha256": source,
                "valid_until": "2026-12-02",
                "human_approval_reference": "fixture-owner-456",
            }
        ],
    }
    (tmp_path / "source.json").write_text(json.dumps(real), encoding="utf-8")
    (tmp_path / "campaign.json").write_text(json.dumps(live), encoding="utf-8")
    (tmp_path / "db.sqlite3").touch()  # An empty DB proves this check never opens it.
    env = {
        "DASHSCOPE_API_KEY": "fixture-secret-value",
        "DASHSCOPE_BASE_URL": ENDPOINT,
        "WEIRAN_EDU_DATABASE": str(tmp_path / "db.sqlite3"),
        "WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT": str(tmp_path / "ledger"),
        "WEIRAN_EDU_REAL_DATA_APPROVAL": str(tmp_path / "source.json"),
        "WEIRAN_EDU_QWEN_LIVE_APPROVAL": str(tmp_path / "campaign.json"),
    }
    return env, live


def test_static_checks_create_no_client_files_or_secrets_in_output(tmp_path, monkeypatch):
    env, live = configured(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("must not construct a network client")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    report = inspect_environment(env, today=TODAY)
    assert report["status"] == "static_checks_passed" and report["model_requests"] == 0
    assert report["checks"]["first_request_budget"]["first_reservation_cny"] == "0.7956064"
    assert report["requires_separate_verification"]
    assert {p.name for p in tmp_path.iterdir()} == {"source.json", "campaign.json", "db.sqlite3"}
    output = json.dumps(report)
    for value in [*env.values(), live["human_approval_reference"], live["campaign_id"], "a" * 64]:
        assert value not in output


def test_old_price_is_stale_and_approval_fingerprint_cannot_extend_it(tmp_path):
    env, live = configured(tmp_path)
    assert REVIEWED_PRICE_20261112.fingerprint != PRICE.fingerprint
    assert REVIEWED_PRICE_20261112.valid_until == "2026-11-19"
    live["price_fingerprint"] = REVIEWED_PRICE_20261112.fingerprint
    (tmp_path / "campaign.json").write_text(json.dumps(live), encoding="utf-8")
    result = inspect_environment(env, today=TODAY)
    assert result["checks"]["campaign_approval"]["status"] == "stale_authorization_or_price"


def test_wrong_region_low_budget_used_approval_and_missing_key_are_distinct(tmp_path):
    env, live = configured(tmp_path)
    env["DASHSCOPE_BASE_URL"] = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    del env["DASHSCOPE_API_KEY"]
    live["cost_cap_cny"] = "0.10"
    (tmp_path / "campaign.json").write_text(json.dumps(live), encoding="utf-8")
    approvals = tmp_path / "ledger" / "approvals"
    approvals.mkdir(parents=True)
    (approvals / (sha256(live["human_approval_reference"].encode()).hexdigest() + ".json")).touch()
    checks = inspect_environment(env, today=TODAY)["checks"]
    assert checks["qwen_settings"]["status"] == "model_or_beijing_endpoint_mismatch"
    assert checks["api_key"]["status"] == "missing"
    assert checks["first_request_budget"]["status"] == "first_reservation_exceeds_approval"
    assert checks["campaign_reuse"]["status"] == "campaign_or_approval_already_used"
