"""B-02-S2: confirmed positions become facts that survive a restart and can be queried."""

from io import BytesIO

from openpyxl import Workbook

CSV = (
    "参赛编号,题号,实得分,OJ Verdict\n"
    "202601010001,P-02,8.25,Accepted\n"
    "202601010002,P-02,6.5,Wrong Answer\n"
    "contest-009,P-09,2,Accepted\n"
).encode()

DIMENSION_XLSX_HEADERS = ["维度", "维度评分", "考号", "评语"]


def _xlsx(headers, rows) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "陌生维度表"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    memory = BytesIO()
    book.save(memory)
    book.close()
    return memory.getvalue()


def _upload(client, data: bytes, name: str, content_type: str) -> dict:
    response = client.post("/api/v1/edu/materials", files=[("file", (name, data, content_type))])
    assert response.status_code == 200, response.text
    return response.json()["materials"][0]


def _decisions(material: dict, meanings: list[str]) -> list[dict]:
    """Resolve locators from the recognized snapshot instead of guessing refs."""
    table = material["tables"][0]["ref"]
    fields = {
        field["meaning"]: field["column"]
        for field in material["fields"]
        if field["column"].startswith(table + ".")
    }
    decisions = [{"kind": "role", "table": table, "value": "evaluation"}]
    decisions += [
        {"kind": "field", "table": table, "column": fields[meaning], "value": meaning}
        for meaning in meanings
        if meaning in fields
    ]
    return decisions


def _confirm(client, source_id: str, scope: str, decisions: list[dict]):
    return client.post(
        "/api/v1/edu/materials/confirm",
        json={"source_id": source_id, "data_scope_id": scope, "decisions": decisions},
    )


def _facts(client, source_id: str, data: bytes, name: str, content_type: str):
    return client.post(
        f"/api/v1/edu/materials/{source_id}/facts",
        files=[("file", (name, data, content_type))],
    )


def _student(runtime, number: str, name: str) -> str:
    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO students (student_pk,public_id,data_scope_id,created_at) "
            "VALUES (?,?,?,?)",
            ("pk-" + number, "PUB-" + number[-4:], runtime.scope, "2026-11-25T00:00:00.000000Z"),
        )
        connection.execute(
            "INSERT OR IGNORE INTO student_identities "
            "(student_pk,data_scope_id,student_number,student_name,created_at) "
            "VALUES (?,?,?,?,?)",
            ("pk-" + number, runtime.scope, number, name, "2026-11-25T00:00:00.000000Z"),
        )
    return "PUB-" + number[-4:]


def test_confirmed_csv_scores_are_readable_and_statistically_usable(client, runtime):
    material = _upload(client, CSV, "陌生判题.csv", "text/csv")
    decisions = _decisions(material, ["student_key", "problem_key", "score", "verdict"])
    confirmed = _confirm(client, material["source_id"], runtime.scope, decisions)
    assert confirmed.status_code == 200, confirmed.text

    written = _facts(client, material["source_id"], CSV, "陌生判题.csv", "text/csv")
    assert written.status_code == 200, written.text
    summary = written.json()
    assert summary["rows_scanned"] == 3
    assert summary["facts_written"] == 3
    assert summary["linked_count"] == 0

    listing = client.get(f"/api/v1/edu/materials/{material['source_id']}/facts").json()
    assert listing["total_count"] == 3
    first = listing["items"][0]
    assert (first["kind"], first["participant_key"]) == ("score", "202601010001")
    assert first["problem_key"] == "P-02" and first["verdict"] == "Accepted"
    assert first["score"] == "8.25" and first["score_value"] == 8.25
    assert first["identity_basis"] == "none" and first["student_id"] is None

    stats = client.get(f"/api/v1/edu/materials/{material['source_id']}/facts/statistics").json()
    assert stats["fact_count"] == 3
    assert stats["participant_count"] == 3
    assert stats["linked_participant_count"] == 0
    assert [p["problem_key"] for p in stats["problems"]] == ["P-02", "P-09"]
    p02 = stats["problems"][0]
    assert (p02["fact_count"], p02["scored_count"]) == (2, 2)
    assert p02["mean"] == "7.38"
    assert p02["verdicts"] == {"Accepted": 1, "Wrong Answer": 1}


def test_dimension_material_becomes_dimension_facts(client, runtime):
    data = _xlsx(
        DIMENSION_XLSX_HEADERS,
        [["代码规范", 88.5, "S-01", "结构清晰"], ["算法效率", 72, "S-02", "可优化"]],
    )
    material = _upload(client, data, "陌生维度表.xlsx", "text/csv")
    decisions = _decisions(material, ["student_key", "dimension", "dimension_score", "feedback"])
    assert _confirm(client, material["source_id"], runtime.scope, decisions).status_code == 200

    response = _facts(client, material["source_id"], data, "陌生维度表.xlsx", "text/csv")
    assert response.status_code == 200, response.text
    assert response.json()["facts_written"] == 2

    listing = client.get(f"/api/v1/edu/materials/{material['source_id']}/facts").json()
    assert {item["kind"] for item in listing["items"]} == {"dimension"}
    assert {item["dimension"] for item in listing["items"]} == {"代码规范", "算法效率"}

    stats = client.get(f"/api/v1/edu/materials/{material['source_id']}/facts/statistics").json()
    assert stats["dimensions"][0]["dimension"] == "代码规范"
    assert stats["dimensions"][0]["mean"] == "88.50"
    assert stats["dimensions"][1]["mean"] == "72.00"


def test_participant_links_only_on_an_exact_scoped_student_number(client, runtime):
    public_id = _student(runtime, "202601010001", "合成甲")
    material = _upload(client, CSV, "陌生判题.csv", "text/csv")
    decisions = _decisions(material, ["student_key", "problem_key", "score", "verdict"])
    assert _confirm(client, material["source_id"], runtime.scope, decisions).status_code == 200
    assert _facts(client, material["source_id"], CSV, "陌生判题.csv", "text/csv").status_code == 200

    listing = client.get(f"/api/v1/edu/materials/{material['source_id']}/facts").json()
    linked = [item for item in listing["items"] if item["participant_key"] == "202601010001"]
    assert linked and linked[0]["student_id"] == public_id
    assert linked[0]["identity_basis"] == "student_number"
    stranger = [item for item in listing["items"] if item["participant_key"] == "contest-009"]
    assert stranger[0]["student_id"] is None and stranger[0]["identity_basis"] == "none"

    stats = client.get(f"/api/v1/edu/materials/{material['source_id']}/facts/statistics").json()
    assert stats["linked_participant_count"] == 1


def test_facts_survive_restart_and_re_ingest_replaces_instead_of_duplicating(
    client, runtime, client_factory
):
    material = _upload(client, CSV, "陌生判题.csv", "text/csv")
    decisions = _decisions(material, ["student_key", "problem_key", "score", "verdict"])
    assert _confirm(client, material["source_id"], runtime.scope, decisions).status_code == 200
    assert _facts(client, material["source_id"], CSV, "陌生判题.csv", "text/csv").status_code == 200
    before = client.get(f"/api/v1/edu/materials/{material['source_id']}/facts").json()

    with client_factory(runtime.token) as restarted:
        after = restarted.get(f"/api/v1/edu/materials/{material['source_id']}/facts").json()
    assert after["items"] == before["items"]
    assert after["total_count"] == 3

    again = _facts(client, material["source_id"], CSV, "陌生判题.csv", "text/csv")
    assert again.status_code == 200
    assert (
        client.get(f"/api/v1/edu/materials/{material['source_id']}/facts").json()["total_count"]
        == 3
    )


def test_unconfirmed_or_different_bytes_never_produce_facts(client, runtime):
    material = _upload(client, CSV, "陌生判题.csv", "text/csv")
    rejected = _facts(client, material["source_id"], CSV, "陌生判题.csv", "text/csv")
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "MATERIAL_SCOPE_REQUIRED"

    decisions = _decisions(material, ["student_key", "score"])
    assert _confirm(client, material["source_id"], runtime.scope, decisions).status_code == 200
    changed = CSV.replace(b"8.25", b"9.25")
    mismatch = _facts(client, material["source_id"], changed, "陌生判题.csv", "text/csv")
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "MATERIAL_CONTENT_MISMATCH"

    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE material_records SET status='revoked' WHERE source_id=?",
            (material["source_id"],),
        )
    revoked = _facts(client, material["source_id"], CSV, "陌生判题.csv", "text/csv")
    assert revoked.status_code == 409
    assert revoked.json()["error"]["code"] == "MATERIAL_REVOKED"


def test_html_table_labelled_by_position_becomes_dimension_facts(client, runtime):
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>能力维度</title></head>"
        "<body><table><tr><th>学号</th><th>能力维度</th><th>维度得分</th><th>评语</th></tr>"
        "<tr><td>202601010001</td><td>代码规范</td><td>88.5</td><td>结构清晰</td></tr>"
        "<tr><td>202601010002</td><td>代码规范</td><td>74</td><td>命名可读</td></tr>"
        "</table></body></html>"
    ).encode()
    material = _upload(client, html, "陌生维度.html", "text/html")
    table = material["tables"][0]["ref"]
    # The snapshot only calls 学号 a student candidate; the rest is named by the teacher.
    decisions = [{"kind": "role", "table": table, "value": "evaluation"}]
    decisions += [
        {"kind": "field", "table": table, "column": f"{table}.C{position}", "value": meaning}
        for position, meaning in (
            (1, "student_key"),
            (2, "dimension"),
            (3, "dimension_score"),
            (4, "feedback"),
        )
    ]
    assert _confirm(client, material["source_id"], runtime.scope, decisions).status_code == 200

    written = _facts(client, material["source_id"], html, "陌生维度.html", "text/html")
    assert written.status_code == 200, written.text
    assert written.json()["facts_written"] == 2

    stats = client.get(f"/api/v1/edu/materials/{material['source_id']}/facts/statistics").json()
    assert stats["problems"] == []
    only = stats["dimensions"][0]
    assert (only["dimension"], only["fact_count"]) == ("代码规范", 2)
    assert (only["mean"], only["minimum"], only["maximum"]) == ("81.25", "74.00", "88.50")
