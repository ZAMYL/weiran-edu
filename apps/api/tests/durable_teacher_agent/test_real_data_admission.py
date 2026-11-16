"""Real batches pass only through an operator admission; default is fail-closed."""

import json
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient
from prototypes.teacher_agent_bridge.provider import (
    ProductionDatabaseProvider,
    RealDataAdmission,
    RealDataSource,
)

from app.core.errors import APIError

from .test_database_profiles import profile_host
from .test_tasks import actor, upload

VALID_UNTIL = "2026-12-31"
CODE = "a" * 64


def admission_for(source_sha256):
    return RealDataAdmission(
        purpose="real_teacher_data",
        human_approval_reference="OWNER-REAL-01",
        independent_review_reference="REVIEW-REAL-01",
        reviewed_code_sha256=CODE,
        valid_until=VALID_UNTIL,
        sources=(
            RealDataSource(
                source_sha256=source_sha256,
                valid_until=VALID_UNTIL,
                human_approval_reference="OWNER-REAL-01",
            ),
        ),
    )


def test_real_batch_is_rejected_without_source_approval(tmp_path):
    fixture, service = profile_host(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture, "balanced72")
    teacher = actor(fixture, service)
    provider = ProductionDatabaseProvider(fixture.app.state.edu_service, admission_for("0" * 64))
    with pytest.raises(APIError) as error:
        provider.capture(teacher, batch["batch_id"], batch["snapshot_id"])
    assert error.value.code == "AI_REAL_DATA_NOT_APPROVED"


def test_approved_real_batch_captures_without_identity_or_comments(tmp_path):
    fixture, service = profile_host(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture, "balanced72")
    teacher = actor(fixture, service)
    with service.store.database.transaction() as connection:
        source_sha = connection.execute(
            "SELECT file_sha256 FROM batches WHERE batch_id=?", (batch["batch_id"],)
        ).fetchone()["file_sha256"]
    provider = ProductionDatabaseProvider(fixture.app.state.edu_service, admission_for(source_sha))
    material, manifest = provider.capture(teacher, batch["batch_id"], batch["snapshot_id"])
    assert len(material.data.participants) == 72
    assert material.data.items
    assert manifest
    serialized = json.dumps(asdict(material.data), ensure_ascii=False, default=str)
    assert "学生姓名" not in serialized
    assert "comment" not in serialized


def test_real_data_admission_file_must_be_outside_the_repository(tmp_path):
    source = "b" * 64
    approval = tmp_path / "real-data-approval.json"
    approval.write_text(
        json.dumps(
            {
                "purpose": "real_teacher_data",
                "human_approval_reference": "OWNER-REAL-01",
                "independent_review_reference": "REVIEW-REAL-01",
                "reviewed_code_sha256": CODE,
                "valid_until": VALID_UNTIL,
                "sources": [
                    {
                        "source_sha256": source,
                        "valid_until": VALID_UNTIL,
                        "human_approval_reference": "OWNER-REAL-01",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    admission = RealDataAdmission.load(approval)
    admission.require(source)
    with pytest.raises(APIError) as error:
        admission.require("c" * 64)
    assert error.value.code == "AI_REAL_DATA_NOT_APPROVED"
