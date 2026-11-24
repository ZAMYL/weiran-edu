"""B-02-S1：统一上传识别接口的定向测试（合成内容）。"""

from io import BytesIO
from zipfile import ZipFile, ZipInfo

from openpyxl import Workbook

URL = "/api/v1/edu/materials/inspect"


def post(client, payload: bytes, name: str = "合成资料.bin", content_type: str | None = None):
    files = {"file": (name, payload, content_type)} if content_type else {"file": (name, payload)}
    return client.post(URL, files=files)


def csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def xlsx_bytes(rows: list[list[object]]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "合成测评"
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def html_table(headers: list[str], rows: list[list[str]]) -> bytes:
    head = "".join(f"<th>{item}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return (
        "<html><body><table class='result'><thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table></body></html>"
    ).encode("utf-8")


def answersheet_zip() -> bytes:
    block = (
        "<div class='flex space-x-2'>"
        "<div class='flex-none font-medium'>7-1</div>"
        "<div class='flex-1 space-y-4'>"
        "<div><span>编译器: GXX</span></div>"
        "<pre class='box'>int main(){ return 0; }</pre>"
        "<table class='result'><thead><tr><th>测试点</th><th>结果</th><th>测试点得分</th>"
        "<th>耗时</th><th>内存</th></tr></thead><tbody>"
        "<tr><td>1</td><td>答案正确</td><td>5</td><td>2.00 ms</td><td>304 KB</td></tr>"
        "</tbody></table></div>"
        "<div class='flex-none min-w-[5rem]'><div>答案正确</div><span>5 / 5</span></div>"
        "</div>"
    )
    page = (
        "<html><body><div class='box'>"
        "<div><span>考生</span><span>S001 同学甲</span></div>"
        "</div><div class='space-y-8'>" + block + "</div></body></html>"
    )
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(
            ZipInfo("合成包/正式答卷/S001-同学甲.html", date_time=(1980, 1, 1, 0, 0, 0)),
            page,
        )
    return stream.getvalue()


def test_csv_is_recognised_with_code_evidence(client):
    response = post(
        client,
        csv_bytes("学号,题号,源代码,得分\nS001,7-1,int main(){},5\nS002,7-1,int main(){},3\n"),
        name="合成.csv",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "csv"
    assert body["supported"] is True
    assert body["role"] == "programming"
    assert body["role_state"] == "candidate"
    assert body["route"] == "material_understanding"
    meanings = {field["meaning"] for field in body["fields"]}
    assert {"student_key", "problem_key", "code", "score"} <= meanings
    assert "code_evidence" in body["analyses"]
    assert body["source_id"] and len(body["source_id"]) == 64


def test_xlsx_is_recognised(client):
    payload = xlsx_bytes([["学号", "题号", "得分"], ["S001", "7-1", 5], ["S002", "7-1", 3]])

    response = post(client, payload, name="合成.xlsx")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "xlsx"
    assert body["supported"] is True
    assert body["role"] == "programming"
    assert body["tables"][0]["rows"] == 2


def test_evaluation_table_role(client):
    payload = html_table(
        ["学号", "维度", "维度评分"], [["S001", "代码实现", "4"], ["S002", "代码实现", "3"]]
    )

    response = post(client, payload, name="合成.html")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "html"
    assert body["role"] == "evaluation"
    assert "five_dimensions" in body["analyses"]


def test_unknown_headers_are_reported_as_pending(client):
    payload = html_table(["甲", "乙"], [["1", "2"]])

    response = post(client, payload, name="陌生.html")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] is None
    assert body["role_state"] == "needs_confirmation"
    codes = {item["code"] for item in body["pending"]}
    assert "FIELD_NEEDS_CONFIRMATION" in codes
    assert "ROLE_UNCONFIRMED" in codes


def test_answersheet_zip_points_to_existing_import_route(client):
    response = post(client, answersheet_zip(), name="合成答题卡.zip")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "zip_html"
    assert body["supported"] is True
    assert body["route"] == "assessment_import"
    assert body["role"] == "programming"
    assert body["tables"][0]["rows"] == 1


def test_unsupported_binary_is_reported(client):
    response = post(client, b"\x89PNG\r\n\x1a\nnot-an-image", name="图片.png")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["supported"] is False
    assert body["route"] == "unsupported"


def test_invalid_token_is_rejected(client_factory):
    with client_factory("invalid-token") as rejected:
        response = rejected.post(URL, files={"file": ("合成.csv", b"a,b\n1,2\n")})

    assert response.status_code == 401
