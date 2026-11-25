"""Check the worker's environment and approval gates without starting a task or using HTTP."""

import json
import os
from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from pathlib import Path

from app.ai.edu.discovery.models import AgentError
from app.ai.edu.models import InsightError
from app.ai.edu.qwen_config import QwenSettings
from app.core.errors import APIError
from pydantic import SecretStr, ValidationError

from prototypes.adaptive_assessment.live.pricing import current_price, reviewed_price
from prototypes.durable_teacher_agent.live_admission import LiveAdmission
from prototypes.qwen_execution.runtime import ROOT, code_fingerprint
from prototypes.teacher_agent_bridge.provider import RealDataAdmission


def _path_state(value: str | None, *, kind: str) -> str:
    if not value:
        return "missing"
    try:
        path = Path(value).expanduser()
        if not path.is_absolute() or path.resolve().is_relative_to(ROOT):
            return "requires_external_absolute_path"
        if kind == "file" and not path.is_file():
            return "file_not_found"
        if kind == "directory" and path.exists() and not path.is_dir():
            return "requires_directory"
    except (OSError, RuntimeError, ValueError):
        return "invalid_path"
    return "ok"


def inspect_environment(env: Mapping[str, str], *, today: date) -> dict:
    checks: dict[str, dict] = {}

    def check(name: str, category: str, status: str, **details: object) -> None:
        checks[name] = {"category": category, "status": status, **details}

    fingerprint = code_fingerprint()
    price = current_price(today)
    check(
        "api_key",
        "configuration",
        "ok" if env.get("DASHSCOPE_API_KEY", "").strip() else "missing",
    )
    endpoint = env.get("DASHSCOPE_BASE_URL", "")
    model = env.get("WEIRAN_QWEN_MODEL", price.model)
    try:
        QwenSettings(
            SecretStr("preflight-placeholder"),
            endpoint,
            model=model,
            output_mode=env.get("WEIRAN_QWEN_OUTPUT_MODE", "json_schema"),
        )
    except InsightError as exc:
        check(
            "qwen_settings",
            "configuration",
            str(exc) if endpoint else "endpoint_missing",
        )
        settings_ok = False
    else:
        # Check the reviewed model and region separately from the snapshot expiry.
        try:
            price.check(model, endpoint, date.fromisoformat(price.checked_on))
        except AgentError:
            check(
                "qwen_settings", "configuration", "model_or_beijing_endpoint_mismatch"
            )
            settings_ok = False
        else:
            check("qwen_settings", "configuration", "ok")
            settings_ok = True

    for name, key, kind in (
        ("database_file", "WEIRAN_EDU_DATABASE", "file"),
        ("ledger_path", "WEIRAN_EDU_CAMPAIGN_LEDGER_ROOT", "directory"),
    ):
        check(name, "configuration", _path_state(env.get(key), kind=kind))
    # Same fallback as install_live_from_env. No DB connection or directory creation.
    state = env.get("WEIRAN_EDU_AI_STATE_DIR")
    if state is None and checks["database_file"]["status"] == "ok":
        state = str(Path(env["WEIRAN_EDU_DATABASE"]).parent / "ai-state")
    check("ai_state_path", "configuration", _path_state(state, kind="directory"))

    check(
        "current_reviewed_price",
        "approval",
        "ok"
        if price.checked_on <= today.isoformat() <= price.valid_until
        else "snapshot_not_current",
        valid_until=price.valid_until,
    )
    real_path = env.get("WEIRAN_EDU_REAL_DATA_APPROVAL")
    real_state = _path_state(real_path, kind="file")
    if real_state == "ok":
        try:
            RealDataAdmission.load(real_path, today=today, code_sha256=fingerprint)
        except APIError as exc:
            real_state = exc.code
        except (ValidationError, OSError, ValueError):
            real_state = "approval_format_or_path_invalid"
    check("source_approval", "approval", real_state)

    live_path = env.get("WEIRAN_EDU_QWEN_LIVE_APPROVAL")
    live_state = _path_state(live_path, kind="file")
    admission = None
    if live_state == "ok":
        try:
            admission = LiveAdmission.load(env, today=today)
        except AgentError as exc:
            live_state = str(exc)
        except (ValidationError, OSError, ValueError):
            live_state = "approval_format_or_path_invalid"
    check("campaign_approval", "approval", live_state)
    if admission is not None:
        approval = admission.approval
        endpoint_matches = (
            sha256(endpoint.rstrip("/").encode()).hexdigest()
            == approval.endpoint_sha256
        )
        check(
            "approved_endpoint",
            "approval",
            "ok"
            if settings_ok and model == approval.model and endpoint_matches
            else "approval_model_or_endpoint_mismatch",
        )
        approved_price = reviewed_price(approval.price_fingerprint)
        reservation_tokens = (
            approved_price.max_input_tokens + approval.max_output_tokens
        )
        reservation_cost = approved_price.cost(
            approved_price.max_input_tokens, approval.max_output_tokens
        )
        check(
            "first_request_budget",
            "approval",
            "ok"
            if reservation_tokens <= approval.total_token_limit
            and reservation_cost <= approval.cost_cap_cny
            else "first_reservation_exceeds_approval",
            max_requests=approval.max_requests,
            max_output_tokens=approval.max_output_tokens,
            first_reservation_tokens=reservation_tokens,
            first_reservation_cny=str(reservation_cost),
        )
        approval_index = sha256(approval.human_approval_reference.encode()).hexdigest()
        try:
            used = (
                admission.ledger_root / (approval.campaign_id + ".jsonl")
            ).exists() or (
                admission.ledger_root / "approvals" / (approval_index + ".json")
            ).exists()
            ledger_state = "campaign_or_approval_already_used" if used else "ok"
        except OSError:
            ledger_state = "ledger_stat_failed"
        check("campaign_reuse", "approval", ledger_state)

    eligible = all(item["status"] == "ok" for item in checks.values())
    return {
        "status": "static_checks_passed" if eligible else "blocked",
        "checked_on": today.isoformat(),
        "code_fingerprint": fingerprint,
        "reviewed_price": {
            "model": price.model,
            "region": "cn-beijing",
            "fingerprint": price.fingerprint,
            "valid_until": price.valid_until,
            "max_input_tokens": price.max_input_tokens,
            "six_request_max_cost_cny_at_800_output": str(
                6 * price.cost(price.max_input_tokens, 800)
            ),
        },
        "checks": checks,
        "requires_separate_verification": [
            "database_schema_and_worker_health",
            "running_service_route_installation",
            "target_batch_snapshot_grant_and_campaign_binding",
            "remote_key_scope_network_and_model_response",
        ],
        "model_requests": 0,
    }


def main() -> int:
    result = inspect_environment(os.environ, today=date.today())
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["status"] == "static_checks_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
