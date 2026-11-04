"""Trusted SQLite admission, revocation and disclosure; no model or JSON source admission."""

from dataclasses import replace

import pytest

from app.core.errors import APIError
from app.edu.question_provider import QuestionAssessmentProvider

from .test_question_xlsx import synthetic


@pytest.fixture
def official(client, runtime):
    response = client.post(
        "/api/v1/edu/imports/preview",
        files={"file": ("synthetic.xlsx", synthetic(), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    view = response.json()
    committed = client.post(
        f"/api/v1/edu/imports/{view['import_id']}/commit",
        json={"preview_version": view["preview_version"]},
    )
    assert committed.status_code == 201, committed.text
    return QuestionAssessmentProvider(runtime.service), committed.json()


def test_verified_dynamic_snapshot_and_witness(official, runtime):
    provider, ref = official
    lease = provider.acquire(runtime.p, ref["batch_id"], ref["snapshot_id"])
    snapshot = provider.read_snapshot(lease)
    assert len(snapshot.items) == 13 and len(snapshot.observations) == 260
    assert {i.kind for i in snapshot.items} == {"question"}
    assert snapshot.items[-1].maximum == "130"
    assert snapshot.remote_model_allowed is False
    witness = provider.replay_witness(lease, snapshot.witnesses[0].reference_id)
    assert witness.source_sha256 == snapshot.source_sha256
    assert witness.cell == "B2" and witness.observation.raw == "2.75"
    with pytest.raises(APIError) as error:
        provider.replay_witness(lease, "unknown")
    assert error.value.status_code == 404
    with pytest.raises(APIError):
        provider.read_snapshot(replace(lease, source_sha256="0" * 64))


def test_no_client_json_or_remote_purpose(official, runtime):
    provider, ref = official
    with pytest.raises(APIError) as error:
        provider.acquire(runtime.p, ref["batch_id"], ref["snapshot_id"], purpose="external_model")
    assert error.value.code == "FORMAL_MODEL_CALL_DISABLED"
    with pytest.raises(APIError):
        provider.acquire({"teacher_id": runtime.teacher}, ref["batch_id"], ref["snapshot_id"])
    with pytest.raises(APIError):
        provider.acquire(runtime.p, ref["batch_id"], "wrong-snapshot")


def test_readonly_grant_revoke_and_token_revocation(official, runtime):
    provider, ref = official
    with pytest.raises(APIError):
        provider.acquire(runtime.q, ref["batch_id"], ref["snapshot_id"])
    runtime.admin.set_member(runtime.other, runtime.scope, can_import=False)
    runtime.admin.grant_read(runtime.other, ref["batch_id"])
    lease = provider.acquire(runtime.q, ref["batch_id"], ref["snapshot_id"])
    snapshot = provider.read_snapshot(lease)
    runtime.admin.revoke_read(runtime.other, ref["batch_id"])
    for operation in (
        lambda: provider.read_snapshot(lease),
        lambda: provider.replay_witness(lease, snapshot.witnesses[0].reference_id),
        lambda: provider.commit_disclosure(lease, ()),
    ):
        with pytest.raises(APIError):
            operation()
    owner = provider.acquire(runtime.p, ref["batch_id"], ref["snapshot_id"])
    runtime.admin.revoke_tokens(runtime.teacher)
    with pytest.raises(APIError) as error:
        provider.read_snapshot(owner)
    assert error.value.status_code == 401


def test_disclosure_survives_new_lease_and_rejects_small_intersection(official, runtime):
    provider, ref = official
    lease = provider.acquire(runtime.p, ref["batch_id"], ref["snapshot_id"])
    snapshot = provider.read_snapshot(lease)
    refs = {w.participant_id: w.reference_id for w in snapshot.witnesses if w.item_id == "Q01"}
    ids = sorted(refs)

    def group(indexes):
        return tuple(refs[ids[i]] for i in indexes)

    provider.commit_disclosure(lease, (group(range(10)),))
    second = QuestionAssessmentProvider(runtime.service)
    next_lease = second.acquire(runtime.p, ref["batch_id"], ref["snapshot_id"])
    second.commit_disclosure(next_lease, (group(range(5, 15)),))
    with pytest.raises(APIError) as error:
        second.commit_disclosure(next_lease, (group([0, 1, 2, 3, 5, 6, 7, 8, 10, 11, 12, 13]),))
    assert error.value.code == "PROVIDER_PRIVACY_LIMITED"
    with pytest.raises(APIError):
        second.commit_disclosure(next_lease, (("client-invented-reference",),))
    second.release(next_lease)
    with pytest.raises(APIError):
        second.read_snapshot(next_lease)
    reopened = second.acquire(runtime.p, ref["batch_id"], ref["snapshot_id"])
    with pytest.raises(APIError):
        second.commit_disclosure(reopened, (group(range(9)),))
