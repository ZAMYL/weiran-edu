"""B-02-S1: confirmations are hash-bound, append-only and owner-only."""

CSV = (
    "OJ Verdict,实得分,小题代码,参赛编号\n"
    "Accepted,7.75,Q-2,contest-001\n"
    "Wrong Answer,3,Q-2,contest-002\n"
    "Timeout,1.5,Q-9,contest-002\n"
).encode()

DECISIONS = [
    {"kind": "role", "table": "F1.T1", "value": "programming"},
    {"kind": "field", "table": "F1.T1", "column": "F1.T1.C2", "value": "score"},
]


def _upload(client) -> dict:
    response = client.post(
        "/api/v1/edu/materials", files=[("file", ("判题导出.csv", CSV, "text/csv"))]
    )
    assert response.status_code == 200
    return response.json()["materials"][0]


def _confirm(client, source_id, scope_id, decisions=None):
    return client.post(
        "/api/v1/edu/materials/confirm",
        json={
            "source_id": source_id,
            "data_scope_id": scope_id,
            "decisions": decisions or DECISIONS,
        },
    )


def test_confirmation_is_recorded_once_with_teacher_and_scope(client, runtime):
    material = _upload(client)

    first = _confirm(client, material["source_id"], runtime.scope)
    assert first.status_code == 200
    view = first.json()
    assert view["status"] == "active"
    assert view["data_scope_id"] == runtime.scope
    assert view["material_id"]
    assert {(item["kind"], item["value"]) for item in view["confirmed"]} == {
        ("role", "programming"),
        ("field", "score"),
    }
    fields = {field["column"]: field for field in view["file"]["fields"]}
    assert fields["F1.T1.C2"]["state"] == "confirmed"

    replay = _confirm(client, material["source_id"], runtime.scope)
    assert replay.status_code == 200
    assert replay.json()["material_id"] == view["material_id"]
    assert replay.json()["confirmed"] == view["confirmed"]


def test_changing_a_confirmation_conflicts_instead_of_overwriting(client, runtime):
    material = _upload(client)
    assert _confirm(client, material["source_id"], runtime.scope).status_code == 200

    conflict = _confirm(
        client,
        material["source_id"],
        runtime.scope,
        [{"kind": "field", "table": "F1.T1", "column": "F1.T1.C2", "value": "verdict"}],
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CONFIRMATION_CONFLICT"


def test_read_returns_confirmed_state_after_a_restart(client, runtime, client_factory):
    material = _upload(client)
    assert _confirm(client, material["source_id"], runtime.scope).status_code == 200
    confirmed = client.get(f"/api/v1/edu/materials/{material['source_id']}").json()

    assert confirmed["file"]["fields"]
    pending = confirmed["file"]["pending"]
    assert "F1.T1.C2" not in pending

    with client_factory(runtime.token) as restarted:
        reread = restarted.get(f"/api/v1/edu/materials/{material['source_id']}")
    assert reread.status_code == 200
    assert reread.json()["file"]["fields"] == confirmed["file"]["fields"]
    assert reread.json()["confirmed"] == confirmed["confirmed"]


def test_another_teacher_cannot_read_or_confirm_the_same_file(client, runtime, client_factory):
    material = _upload(client)
    with client_factory(runtime.other_token) as other:
        assert other.get(f"/api/v1/edu/materials/{material['source_id']}").status_code == 404
        assert _confirm(other, material["source_id"], runtime.scope).status_code == 404


def test_confirmation_requires_scope_membership(client, runtime):
    material = _upload(client)
    response = _confirm(client, material["source_id"], "00000000-0000-0000-0000-000000000000")
    assert response.status_code == 403


def test_unknown_locator_or_value_is_rejected(client, runtime):
    material = _upload(client)

    unknown = _confirm(
        client,
        material["source_id"],
        runtime.scope,
        [{"kind": "field", "table": "F1.T1", "column": "F9.T9.C9", "value": "score"}],
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "MATERIAL_VALUE_INVALID"

    wrong_table = _confirm(
        client,
        material["source_id"],
        runtime.scope,
        [{"kind": "role", "table": "F9.T9", "value": "programming"}],
    )
    assert wrong_table.status_code == 422
    assert wrong_table.json()["error"]["code"] == "MATERIAL_LOCATOR_UNKNOWN"


def test_revoked_record_rejects_further_confirmation(client, runtime):
    material = _upload(client)
    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE material_records SET status='revoked' WHERE source_id=?",
            (material["source_id"],),
        )

    response = _confirm(client, material["source_id"], runtime.scope)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MATERIAL_REVOKED"
