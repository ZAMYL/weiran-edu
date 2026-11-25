"""B-02-S1 acceptance over real HTTP: bytes in, recognition out, nothing confirmed."""

import zipfile
from io import BytesIO

from openpyxl import Workbook


def _xlsx() -> bytes:
    book = Workbook()
    grades = book.active
    grades.title = "本轮计分明细"
    grades.append(["成绩", "题号", "考号"])
    grades.append([8.25, "P-02", "S001"])
    grades.append([6.5, "P-02", "S002"])
    reviews = book.create_sheet("评阅员复核")
    reviews.append(["评语", "考号", "维度评分"])
    reviews.append(["合成评语", "S001", 4.25])
    reviews.append(["合成评语", "S002", 3.5])
    memory = BytesIO()
    book.save(memory)
    book.close()
    return memory.getvalue()


def _upload(client, files):
    return client.post("/api/v1/edu/materials", files=files)


def test_unfamiliar_xlsx_is_recognized_and_left_unconfirmed(client):
    response = _upload(client, [("file", ("陌生成绩单.xlsx", _xlsx(), "text/csv"))])

    assert response.status_code == 200
    body = response.json()
    assert body["coverage"] == "unknown"
    assert body["authority"] == "none"
    material = body["materials"][0]
    assert material["kind"] == "xlsx"
    assert material["status"] == "recognized"
    assert material["source_id"].startswith("sha256:")
    assert len(material["tables"]) == 2
    assert material["tables"][0]["name"] == "本轮计分明细"
    assert material["tables"][0]["columns"][0]["header"] == "成绩"
    assert material["roles"][0]["state"] == "candidate"
    assert {field["meaning"] for field in material["fields"]} >= {"score", "student_key"}
    assert all(field["state"] == "candidate" for field in material["fields"])
    assert material["pending"]
    assert all(not analysis["available"] for analysis in material["analyses"])
    assert body["pending"]


def test_unfamiliar_csv_reports_actual_fields_and_handoff_analysis(client):
    data = (
        "OJ Verdict,实得分,小题代码,参赛编号\n"
        "Accepted,7.75,Q-2,contest-001\n"
        "Wrong Answer,3,Q-2,contest-002\n"
        "Timeout,1.5,Q-9,contest-002\n"
    ).encode()
    response = _upload(client, [("file", ("判题导出.csv", data, "text/csv"))])

    assert response.status_code == 200
    material = response.json()["materials"][0]
    assert material["kind"] == "csv"
    assert {field["meaning"] for field in material["fields"]} == {
        "student_key",
        "problem_key",
        "score",
        "verdict",
    }
    assert {role["role"] for role in material["roles"]} == {"programming"}
    features = next(item for item in material["analyses"] if item["name"] == "code_error_patterns")
    assert features["available"] is False
    assert "confirmed_field:code" in features["missing"]


def test_ambiguous_fields_come_back_as_pending_confirmation(client):
    left = "X档,Y额,Z栏\nlocal-01,10,observable\n,,only-observation\nlocal-03,8,other\n".encode()
    right = "甲列,乙列\nlocal-01,qualifier\nlocal-03,other\n".encode()
    response = _upload(
        client,
        [("file", ("左.csv", left, "text/csv")), ("file", ("右.csv", right, "text/csv"))],
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["materials"]) == 2
    assert body["materials"][0]["fields"] == []
    assert {"F1.T1.C2", "F1.T1.C3"} <= set(body["materials"][0]["pending"])
    assert len({item["source_id"] for item in body["materials"]}) == 2


def test_archive_is_handed_to_the_existing_answer_sheet_path(client):
    memory = BytesIO()
    with zipfile.ZipFile(memory, "w") as archive:
        archive.writestr("答卷.html", "<table><tr><td>考号</td></tr></table>")
    response = _upload(client, [("file", ("答卷包.zip", memory.getvalue(), "application/zip"))])

    assert response.status_code == 200
    body = response.json()
    assert body["materials"] == []
    assert body["archives"][0]["kind"] == "zip_html"
    assert body["archives"][0]["route"] == "/api/v1/edu/assessments"


def test_unsupported_kind_is_named_not_disguised_as_recognized(client):
    response = _upload(client, [("file", ("截图.png", b"\x89PNG\r\n\x1a\n\x00\x00", "image/png"))])

    assert response.status_code == 200
    material = response.json()["materials"][0]
    assert material["status"] == "unsupported"
    assert material["error"] == "EXTRACTION_UNSUPPORTED_KIND"


def test_missing_credentials_are_rejected(client):
    response = _upload(client, [("file", ("判题导出.csv", b"a,b\n1,2\n", "text/csv"))])
    assert response.status_code == 200

    anonymous = client.post(
        "/api/v1/edu/materials",
        files={"file": ("判题导出.csv", b"a,b\n1,2\n", "text/csv")},
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert anonymous.status_code == 401
