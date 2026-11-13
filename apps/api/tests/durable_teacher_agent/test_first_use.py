"""The demo operator's first-use handoff keeps import and purpose boundaries distinct."""

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from prototypes.durable_teacher_agent.first_use import FirstUseImportService

from app.core.errors import APIError

from .test_tasks import QUESTION, actor, setup


def preview(client, raw):
    response = client.post("/api/v1/edu/imports/preview", files={"file": ("sample.xlsx", raw)})
    assert response.status_code == 201, response.text
    preview = response.json()["import"]
    return (
        f"/api/v1/edu/imports/{preview['import_id']}/commit",
        {"preview_version": preview["preview_version"], "acknowledge_warnings": True},
    )


def test_first_use_handoff_recovers_without_resetting_purpose(tmp_path, monkeypatch):
    fixture, service = setup(tmp_path)
    teacher = actor(fixture, service)
    fixture.app.state.edu_service = FirstUseImportService(
        fixture.app.state.edu_service, service.store, teacher.teacher_id, fixture.books["normal"]
    )
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        commit, payload = preview(client, fixture.books["normal"])

        def unavailable(*args, **kwargs):
            raise APIError(503, "DATABASE_UNAVAILABLE", "测试交接失败")

        with monkeypatch.context() as scoped:
            scoped.setattr(service.store, "grant", unavailable)
            assert client.post(commit, json=payload).status_code == 503
            batches = client.get("/api/v1/edu/batches").json()
            assert batches["total_count"] == 1
        response = client.post(commit, json=payload)
        assert response.status_code == 200
        ref = response.json()
        assert ref["result"] == "replayed"
        with service.store.database.transaction() as connection:
            grant = connection.execute(
                "SELECT grant_id FROM ai_synthetic_grants WHERE batch_id=? AND snapshot_id=?",
                (ref["batch_id"], ref["snapshot_id"]),
            ).fetchone()["grant_id"]
        service.store.revoke(grant)
        assert client.post(commit, json=payload).status_code == 200

        path = f"/api/v1/edu/batches/{ref['batch_id']}/ai-investigation-tasks"
        response = client.post(
            path,
            json={
                "snapshot_id": ref["snapshot_id"],
                "question": QUESTION,
                "idempotency_key": "first-purpose-revoked",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AI_PURPOSE_REVOKED"
        with service.store.database.transaction() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM ai_synthetic_grants WHERE revoked=1"
                ).fetchone()[0]
                == 1
            )
            assert connection.execute("SELECT count(*) FROM ai_synthetic_tasks").fetchone()[0] == 0


@pytest.mark.parametrize("case", ["other_teacher", "other_source", "equivalent_unregistered"])
def test_first_use_policy_is_bound_to_teacher_and_original_file(tmp_path, case):
    fixture, service = setup(tmp_path)
    intended = actor(fixture, service)
    approved = fixture.books["normal"]
    fixture.app.state.edu_service = FirstUseImportService(
        fixture.app.state.edu_service, service.store, intended.teacher_id, approved
    )
    token = fixture.token
    if case == "other_teacher":
        teacher = fixture.admin.create_teacher("other-synthetic-owner")
        scope = fixture.admin.create_scope("另一个合成来源")
        fixture.admin.set_member(teacher, scope, can_import=True)
        token = fixture.admin.issue_token(teacher)
    raw = fixture.books["fixed"] if case == "other_source" else approved
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + token}) as client:
        if case == "equivalent_unregistered":
            # Archive metadata changes without changing the content used for deduplication.
            with BytesIO(approved) as source, BytesIO() as out:
                book = load_workbook(source)
                book.properties.title = "Unregistered synthetic archive variant"
                book.save(out)
                book.close()
                variant = out.getvalue()
            commit, payload = preview(client, variant)
            original = client.post(commit, json=payload)
            assert original.status_code == 201
        commit, payload = preview(client, raw)
        response = client.post(commit, json=payload)
        assert response.status_code == (200 if case == "equivalent_unregistered" else 201)
        if case == "equivalent_unregistered":
            assert response.json()["batch_id"] == original.json()["batch_id"]
        with service.store.database.transaction() as connection:
            assert connection.execute("SELECT count(*) FROM ai_synthetic_grants").fetchone()[0] == 0
