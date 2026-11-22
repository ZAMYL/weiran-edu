"""Prepare, preflight, run once, or replay the isolated LIVE-01 technical campaign."""

import argparse
import asyncio
import json
import os
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.ai.edu.discovery.models import AgentError
from app.ai.edu.models import InsightError
from app.ai.edu.qwen_config import QwenSettings
from app.core.config import Settings
from app.core.errors import APIError
from app.edu.assessments import AssessmentReader
from app.edu.auth import TeacherAdmin, authenticate
from app.edu.originals import OriginalStore
from app.main import create_app
from app.storage.database import Database
from fastapi.testclient import TestClient
from pydantic import ValidationError

from prototypes.adaptive_assessment.live.pricing import REVIEWED_PRICE_20261119
from prototypes.durable_teacher_agent.live_admission import LEDGER_ROOT_ENV, REPOSITORY
from prototypes.qwen_execution.runtime import code_fingerprint

from .database_evidence import AssessmentEvidenceProvider
from .programming_evidence import EvidenceScope
from .programming_live import (
    APPROVAL_ENV,
    PURPOSE,
    ZIP_SHA256,
    ProgrammingLiveAdmission,
    ProgrammingLiveInput,
    ledger_fingerprint,
    replay_programming_live,
    run_programming_live,
)
from .programming_live_fixture import FIXTURE_NAME, synthetic_zip

BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _prepare(root: Path) -> dict:
    root.mkdir(mode=0o700)  # Exclusive. Never replace an enrolled assessment or token.
    database = Database(root / "synthetic.sqlite3")
    database.migrate()
    admin = TeacherAdmin(database)
    teacher = admin.create_teacher("live-01-synthetic")
    data_scope = admin.create_scope("LIVE-01 synthetic assessment")
    admin.set_member(teacher, data_scope, can_import=True)
    token = admin.issue_token(teacher, hours=168)
    with TestClient(
        create_app(Settings(environment="test", database_path=database.path))
    ) as client:
        response = client.post(
            "/api/v1/edu/assessments",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (FIXTURE_NAME, synthetic_zip(), "application/zip")},
        )
        if response.status_code != 201:
            raise AgentError(f"synthetic_upload_http_{response.status_code}")
        assessment = response.json()["assessment_id"]
    reader = AssessmentReader(database, originals=OriginalStore(root / "originals"))
    overview = reader.overview(authenticate(database, token), assessment)
    if overview.counts_provisional or overview.source_snapshot_id is None:
        raise AgentError("synthetic_registration_not_confirmed")
    scope = {
        "teacher_id": teacher,
        "task_id": "live-01-" + uuid4().hex,
        "assessment_id": assessment,
        "snapshot_id": overview.source_snapshot_id,
    }
    fd = os.open(root / "teacher.key", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    (root / "scope.json").write_text(json.dumps(scope), encoding="utf-8")
    return _preflight(root)


def _binding(root: Path) -> ProgrammingLiveInput:
    admission = ProgrammingLiveAdmission.load()
    settings = QwenSettings.from_env()
    database = Database(root / "synthetic.sqlite3")
    token = (root / "teacher.key").read_text(encoding="utf-8")
    scope = json.loads((root / "scope.json").read_text(encoding="utf-8"))
    principal = authenticate(database, token)
    if scope["teacher_id"] != principal.teacher_id:
        raise AgentError("programming_live_scope_mismatch")
    source = EvidenceScope(
        principal.teacher_id, scope["task_id"], scope["assessment_id"], scope["snapshot_id"]
    )
    reader = AssessmentReader(database, originals=OriginalStore(root / "originals"))
    return ProgrammingLiveInput(
        AssessmentEvidenceProvider(reader, principal, source),
        admission,
        settings,
        lambda: authenticate(database, token),
    )


def _preflight(root: Path) -> dict:
    price = REVIEWED_PRICE_20261119
    scope = json.loads((root / "scope.json").read_text(encoding="utf-8"))
    settings_present = {
        key: bool(os.environ.get(key, "").strip())
        for key in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", APPROVAL_ENV, LEDGER_ROOT_ENV)
    }
    result = {
        "network_attempted": False,
        "scope": scope,
        "purpose": PURPOSE,
        "model": price.model,
        "beijing_endpoint_sha256": sha256(BASE.encode()).hexdigest(),
        "code_sha256": code_fingerprint(),
        "price_sha256": price.fingerprint,
        "price_valid_until": price.valid_until,
        "synthetic_zip_sha256": ZIP_SHA256,
        "max_six_request_reservation_cny": str(price.cost(price.max_input_tokens, 800) * 6),
        "process_configuration_present": settings_present,
        "ready": False,
    }
    root_env = os.environ.get(LEDGER_ROOT_ENV, "")
    if root_env and Path(root_env).is_absolute():
        result["ledger_root_sha256"] = ledger_fingerprint(Path(root_env))
    if all(settings_present.values()):
        binding = _binding(root)
        binding.source()
        if (
            binding.admission.ledger_root / (binding.admission.approval.campaign_id + ".jsonl")
        ).exists():
            raise AgentError("campaign_already_consumed")
        result["ready"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "preflight", "run", "replay"))
    parser.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.state_dir.expanduser()
    if not root.is_absolute() or root.resolve().is_relative_to(REPOSITORY):
        parser.error("use an absolute, private state directory outside the repository")
    root = root.resolve()
    try:
        if args.command == "prepare":
            result = _prepare(root)
        elif args.command == "preflight":
            result = _preflight(root)
        elif args.command == "replay":
            result = replay_programming_live(_binding(root))
        else:
            result = asyncio.run(run_programming_live(_binding(root)))
    except (AgentError, InsightError, APIError, ValidationError, OSError):
        # Don't print exception messages with approval fields, input paths or keys.
        import sys

        error = sys.exception()
        code = (
            type(error).__name__
            if not isinstance(error, (AgentError, InsightError, APIError))
            else error.code
            if isinstance(error, APIError)
            else str(error)
        )
        print(json.dumps({"state": "rejected", "error_code": code}))
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return int(args.command == "run" and result["state"] != "completed")


if __name__ == "__main__":
    raise SystemExit(main())
