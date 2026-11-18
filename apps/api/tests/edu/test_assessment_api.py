"""答题卡导入的 HTTP 接口：直入、去重、格式拒绝与鉴权。"""

from io import BytesIO
from zipfile import ZipFile


def student_html(*, key: str = "S001", name: str = "周同学", label: str = "7-1") -> str:
    return (
        "<html><body><div class='box'>"
        f"<div><span>考生</span><span>{key} {name}</span></div>"
        "<div><span>试卷总分</span><span>235</span></div>"
        "<div><span>考生得分</span><span>76</span></div>"
        "</div>"
        "<div class='space-y-8'><div class='flex space-x-2'>"
        f"<div class='flex-none font-medium'>{label}</div>"
        "<div class='flex-1 space-y-4'>"
        "<div><span>编译器: GXX</span></div>"
        "<pre class='box font-mono'>int main(){ return 0; }</pre>"
        "<table class='result'><tbody>"
        "<tr><td>1</td><td>答案正确</td><td>5</td><td>2.00 ms</td><td>304 KB</td></tr>"
        "</tbody></table>"
        "</div>"
        "<div class='flex-none min-w-[5rem]'><div>答案正确</div><span>5 / 5</span></div>"
        "</div></div></body></html>"
    )


def answersheet_zip() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("包/正式答卷/S001.html", student_html())
        archive.writestr("包/正式答卷/S002.html", student_html(key="S002", name="吕同学"))
        archive.writestr("包/验题测试组/T001.html", student_html(key="T001", name="叶同学"))
    return stream.getvalue()


URL = "/api/v1/edu/assessments"


def test_answersheet_is_imported_directly(client, runtime):
    response = client.post(URL, files={"file": ("竞赛.zip", answersheet_zip())})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["duplicate"] is False
    assert (body["student_count"], body["problem_count"]) == (2, 1)
    assert (body["submission_count"], body["observation_count"]) == (2, 2)
    assert response.headers["Cache-Control"] == "no-store"


def test_same_file_is_reported_as_duplicate(client):
    payload = answersheet_zip()

    first = client.post(URL, files={"file": ("竞赛.zip", payload)})
    second = client.post(URL, files={"file": ("竞赛.zip", payload)})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["assessment_id"] == first.json()["assessment_id"]


def test_spreadsheet_is_rejected_here(client, book, pack):
    response = client.post(URL, files={"file": ("合成测评.xlsx", pack(book))})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_ADAPTER_FOR_ASSET"
    assert "xlsx" in response.json()["error"]["message"]


def test_unknown_field_is_rejected(client):
    response = client.post(
        URL,
        data={"revision_of_batch_id": "x"},
        files={"file": ("竞赛.zip", answersheet_zip())},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
