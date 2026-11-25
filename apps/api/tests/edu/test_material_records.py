"""B-02-S1: owner listing, canonical source IDs and visible confirmation audit."""

CSV = (
    "OJ Verdict,实得分,小题代码,参赛编号\n"
    "Accepted,7.75,Q-2,contest-001\n"
    "Wrong Answer,3,Q-2,contest-002\n"
    "Timeout,1.5,Q-9,contest-002\n"
).encode()
OTHER_CSV = (
    "OJ Verdict,实得分,小题代码,参赛编号\n"
    "Accepted,9,Q-1,contest-101\n"
    "Wrong Answer,0,Q-1,contest-102\n"
).encode()
DECISIONS = [
    {"kind": "role", "table": "F1.T1", "value": "programming"},
    {"kind": "field", "table": "F1.T1", "column": "F1.T1.C2", "value": "score"},
]


def _upload(client, payload: bytes = CSV, name: str = "判题导出.csv") -> dict:
    response = client.post("/api/v1/edu/materials", files=[("file", (name, payload, "text/csv"))])
    assert response.status_code == 200
    return response.json()["materials"][0]


def _confirm(client, source_id, scope_id):
    return client.post(
        "/api/v1/edu/materials/confirm",
        json={"source_id": source_id, "data_scope_id": scope_id, "decisions": DECISIONS},
    )


def test_duplicate_bytes_in_one_request_share_one_readable_source_id(client, runtime):
    """The same file twice collapses onto one row; both entries must report it."""
    response = client.post(
        "/api/v1/edu/materials",
        files=[
            ("file", ("甲班.csv", CSV, "text/csv")),
            ("file", ("乙班.csv", CSV, "text/csv")),
        ],
    )
    assert response.status_code == 200
    materials = response.json()["materials"]
    assert [item["file_name"] for item in materials] == ["甲班.csv", "乙班.csv"]
    source_ids = {item["source_id"] for item in materials}
    assert len(source_ids) == 1
    table_refs = {item["tables"][0]["ref"] for item in materials}
    assert table_refs == {"F1.T1"}
    source_id = source_ids.pop()
    assert "#" not in source_id

    assert _confirm(client, source_id, runtime.scope).status_code == 200
    read = client.get(f"/api/v1/edu/materials/{source_id}")
    assert read.status_code == 200
    assert read.json()["status"] == "active"


def test_list_returns_own_records_with_paging_and_status(client, runtime, client_factory):
    first = _upload(client, CSV, "判题导出.csv")
    second = _upload(client, OTHER_CSV, "另一份.csv")
    assert first["source_id"] != second["source_id"]

    listing = client.get("/api/v1/edu/materials").json()
    assert listing["total_count"] == 2
    assert {item["source_id"] for item in listing["items"]} == {
        first["source_id"],
        second["source_id"],
    }

    page = client.get("/api/v1/edu/materials", params={"offset": 1, "limit": 1}).json()
    assert page["total_count"] == 2
    assert len(page["items"]) == 1

    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE material_records SET status='revoked' WHERE source_id=?",
            (first["source_id"],),
        )
    revoked = client.get("/api/v1/edu/materials", params={"status": "revoked"}).json()
    assert revoked["total_count"] == 1
    assert revoked["items"][0]["source_id"] == first["source_id"]
    active = client.get("/api/v1/edu/materials", params={"status": "active"}).json()
    assert active["total_count"] == 1

    with client_factory(runtime.other_token) as other:
        assert other.get("/api/v1/edu/materials").json()["total_count"] == 0


def test_confirmations_expose_teacher_time_and_basis(client, runtime):
    material = _upload(client)
    assert _confirm(client, material["source_id"], runtime.scope).status_code == 200

    view = client.get(f"/api/v1/edu/materials/{material['source_id']}").json()
    assert {(item["kind"], item["value"]) for item in view["confirmed"]} == {
        ("role", "programming"),
        ("field", "score"),
    }
    assert len(view["confirmations"]) == 2
    field = next(item for item in view["confirmations"] if item["kind"] == "field")
    assert field["table"] == "F1.T1"
    assert field["column"] == "F1.T1.C2"
    assert field["value"] == "score"
    assert field["confirmed_by"] == runtime.teacher
    assert field["confirmed_at"].endswith("Z")
    assert field["basis"] == "teacher_review"
    role = next(item for item in view["confirmations"] if item["kind"] == "role")
    assert role["column"] is None

    # 同一位置重复确认不新增审计行。
    assert _confirm(client, material["source_id"], runtime.scope).status_code == 200
    replay = client.get(f"/api/v1/edu/materials/{material['source_id']}").json()
    assert replay["confirmations"] == view["confirmations"]
