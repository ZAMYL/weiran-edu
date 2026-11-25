"""B-02-S1 加固：快照指纹、失效确认、重传复活与同空间共享读。"""

import hashlib

from app.edu.material_models import MaterialFile

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


def _upload(client, payload: bytes = CSV, name: str = "判题导出.csv") -> dict:
    response = client.post("/api/v1/edu/materials", files=[("file", (name, payload, "text/csv"))])
    assert response.status_code == 200
    return response.json()["materials"][0]


def _confirm(client, source_id, scope_id, *, decisions=None, plan_sha256=None):
    body = {
        "source_id": source_id,
        "data_scope_id": scope_id,
        "decisions": decisions or DECISIONS,
    }
    if plan_sha256 is not None:
        body["plan_sha256"] = plan_sha256
    return client.post("/api/v1/edu/materials/confirm", json=body)


def test_reupload_revives_a_revoked_record(client, runtime):
    material = _upload(client)
    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE material_records SET status='revoked' WHERE source_id=?",
            (material["source_id"],),
        )
    assert _confirm(client, material["source_id"], runtime.scope).status_code == 409

    again = _upload(client)
    assert again["source_id"] == material["source_id"]
    assert client.get(f"/api/v1/edu/materials/{material['source_id']}").json()["status"] == "active"
    assert _confirm(client, material["source_id"], runtime.scope).status_code == 200


def test_confirm_rejects_a_snapshot_fingerprint_that_moved(client, runtime):
    material = _upload(client)
    view = client.get(f"/api/v1/edu/materials/{material['source_id']}").json()
    fingerprint = view["plan_sha256"]
    assert fingerprint and len(fingerprint) == 64

    stale = _confirm(client, material["source_id"], runtime.scope, plan_sha256="0" * 64)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "MATERIAL_PLAN_STALE"

    fresh = _confirm(client, material["source_id"], runtime.scope, plan_sha256=fingerprint)
    assert fresh.status_code == 200
    assert fresh.json()["confirmed"]


def test_changed_snapshot_invalidates_previous_confirmations(client, runtime):
    from app.edu.materials import MaterialIntake

    material = _upload(client)
    assert _confirm(client, material["source_id"], runtime.scope).status_code == 200

    view = MaterialFile.model_validate(
        client.get(f"/api/v1/edu/materials/{material['source_id']}").json()["file"]
    )
    changed = view.model_copy(deep=True)
    changed.tables[0].columns[0].header = "OJ 判定（新导出）"

    intake = MaterialIntake(runtime.db, clock=lambda: runtime.clock[0])
    rows = intake.record(runtime.p, [(hashlib.sha256(CSV).hexdigest(), changed)])
    material_id, source_id = rows[0]
    assert material_id
    assert source_id == material["source_id"]

    readback = client.get(f"/api/v1/edu/materials/{material['source_id']}").json()
    assert readback["confirmed"] == []
    assert readback["confirmations"] == []
    with runtime.db.transaction() as connection:
        stale = [
            row["stale"]
            for row in connection.execute(
                "SELECT stale FROM material_confirmations WHERE material_id=?",
                (readback["material_id"],),
            ).fetchall()
        ]
    assert stale and set(stale) == {1}


def test_confirmed_role_for_a_removed_table_is_not_resurrected(client, runtime):
    material = _upload(client)
    assert _confirm(client, material["source_id"], runtime.scope).status_code == 200

    empty = MaterialFile(
        file_name="判题导出.csv",
        kind="csv",
        byte_size=len(CSV),
        source_id=material["source_id"],
        status="recognized",
    ).model_dump_json()
    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE material_records SET plan_json=? WHERE source_id=?",
            (empty, material["source_id"]),
        )

    readback = client.get(f"/api/v1/edu/materials/{material['source_id']}").json()
    assert readback["file"]["tables"] == []
    assert [role for role in readback["file"]["roles"] if role["table"] == "F1.T1"] == []
