"""答题卡导入的 HTTP 接口：直入、去重、格式拒绝与鉴权。"""

from io import BytesIO
from zipfile import ZipFile


def student_html(
    *, key: str = "S001", name: str = "周同学", label: str = "7-1", failing: bool = False
) -> str:
    status = "答案错误" if failing else "答案正确"
    score = "0" if failing else "5"
    ratio = "0 / 5" if failing else "5 / 5"
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
        f"<tr><td>1</td><td>{status}</td><td>{score}</td><td>2.00 ms</td><td>304 KB</td></tr>"
        "</tbody></table>"
        "</div>"
        f"<div class='flex-none min-w-[5rem]'><div>{status}</div><span>{ratio}</span></div>"
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


def create_assessment(client) -> str:
    response = client.post(
        URL,
        data={"title": "合成竞赛"},
        files={"file": ("竞赛.zip", answersheet_zip())},
    )
    assert response.status_code == 201, response.text
    return response.json()["assessment_id"]


def test_overview_reports_counts(client):
    assessment_id = create_assessment(client)

    response = client.get(f"{URL}/{assessment_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "合成竞赛"
    assert (body["student_count"], body["problem_count"]) == (2, 1)
    assert (body["submission_count"], body["observation_count"]) == (2, 2)


def test_participants_are_listed_with_original_names(client):
    assessment_id = create_assessment(client)

    response = client.get(f"{URL}/{assessment_id}/students")

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 2
    # 验题组 T001 已被排除，只剩两位参赛者，姓名学号按原值展示
    assert [(item["external_key"], item["display_name"]) for item in body["items"]] == [
        ("S001", "周同学"),
        ("S002", "吕同学"),
    ]


def test_student_detail_returns_code_and_observations(client):
    assessment_id = create_assessment(client)
    participants = client.get(f"{URL}/{assessment_id}/students").json()["items"]
    student_pk = participants[0]["student_pk"]

    response = client.get(f"{URL}/{assessment_id}/students/{student_pk}")

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "周同学"
    assert len(body["submissions"]) == 1
    submission = body["submissions"][0]
    assert submission["problem_label"] == "7-1"
    assert submission["language"] == "GXX"
    assert submission["code"] == "int main(){ return 0; }"
    assert submission["verdict"] == "答案正确"
    assert submission["score"] == 5.0
    assert submission["observations"] == [
        {
            "test_index": 1,
            "status": "答案正确",
            "score": 5.0,
            "duration_ms": 2,
            "memory_kb": 304,
        }
    ]


def test_other_teacher_cannot_read_the_assessment(client, client_factory, runtime):
    assessment_id = create_assessment(client)

    with client_factory(runtime.other_token) as other:
        detail = other.get(f"{URL}/{assessment_id}")
        students = other.get(f"{URL}/{assessment_id}/students")

    assert detail.status_code == 404
    assert students.status_code == 404


def test_unknown_assessment_returns_404(client):
    response = client.get(f"{URL}/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def one_student_zip(key: str, name: str) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(f"包/正式答卷/{key}.html", student_html(key=key, name=name))
    return stream.getvalue()


def test_multiple_packages_are_merged(client):
    first = one_student_zip("S010", "钱同学")
    second = one_student_zip("S011", "孙同学")

    response = client.post(
        URL,
        data={"title": "合成竞赛"},
        files=[("file", ("第一包.zip", first)), ("file", ("第二包.zip", second))],
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["student_count"], body["submission_count"]) == (2, 2)

    students = client.get(f"{URL}/{body['assessment_id']}/students").json()["items"]
    assert [item["external_key"] for item in students] == ["S010", "S011"]


def test_same_set_of_packages_is_reported_as_duplicate(client):
    files = [
        ("file", ("a.zip", one_student_zip("S020", "李同学"))),
        ("file", ("b.zip", one_student_zip("S021", "王同学"))),
    ]

    first = client.post(URL, files=files)
    second = client.post(URL, files=files)

    assert first.status_code == 201, first.text
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["assessment_id"] == first.json()["assessment_id"]


def test_duplicate_participant_across_packages_is_rejected(client):
    files = [
        ("file", ("a.zip", one_student_zip("S030", "周同学"))),
        ("file", ("b.zip", one_student_zip("S030", "周同学"))),
    ]

    response = client.post(URL, files=files)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ANSWERSHEET_DUPLICATE_PARTICIPANT"


def test_problem_patterns_summarise_verdicts_and_test_points(client):
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("包/正式答卷/S001.html", student_html())
        archive.writestr(
            "包/正式答卷/S002.html", student_html(key="S002", name="吕同学", failing=True)
        )
    created = client.post(URL, files={"file": ("竞赛.zip", stream.getvalue())})
    assessment_id = created.json()["assessment_id"]

    response = client.get(f"{URL}/{assessment_id}/patterns")

    assert response.status_code == 200
    pattern = response.json()["items"][0]
    assert pattern["problem_label"] == "7-1"
    assert pattern["submission_count"] == 2
    assert pattern["full_pass_count"] == 1
    assert pattern["zero_score_count"] == 1
    assert pattern["average_score"] == 2.5
    assert {item["status"]: item["count"] for item in pattern["verdict_counts"]} == {
        "答案正确": 1,
        "答案错误": 1,
    }
    assert pattern["test_point_count"] == 1
    assert pattern["failure_count"] == 1
    assert pattern["failure_rate"] == 0.5
    assert pattern["worst_test_points"] == [{"test_index": 1, "failure_count": 1}]
