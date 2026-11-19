"""答题卡证据入库：单事务写入、重复上传去重、学生身份复用。"""

from io import BytesIO
from zipfile import ZipFile

import pytest

from app.core.errors import APIError
from app.edu.assessments import AssessmentService
from app.edu.auth import authenticate
from app.importing.edu.answersheet import parse_answersheet


def student_html(*, key: str = "S001", name: str = "周同学", label: str = "7-1") -> str:
    return (
        "<html><body><div class='box'>"
        "<div><span>答题时长</span><span>63分钟</span></div>"
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


def package(entries: dict[str, str], name: str = "包") -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for path, payload in entries.items():
            archive.writestr(f"{name}/{path}", payload)
    return stream.getvalue()


def one_student(**fields) -> bytes:
    return package({"正式答卷/S001.html": student_html(**fields)})


def service(runtime) -> AssessmentService:
    return AssessmentService(runtime.db)


def create(runtime, data: bytes, *, title: str = "合成竞赛", principal=None):
    return service(runtime).create_answersheet(
        principal or runtime.p,
        data,
        data_scope_id=runtime.scope,
        title=title,
        course=None,
        assessment_date=None,
        evidence=parse_answersheet(data),
    )


def count(runtime, table: str) -> int:
    with runtime.db.transaction() as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_create_writes_all_evidence_tables(runtime):
    summary = create(runtime, one_student())

    assert summary.duplicate is False
    assert (summary.student_count, summary.problem_count) == (1, 1)
    assert (summary.submission_count, summary.observation_count) == (1, 1)
    assert count(runtime, "assessments") == 1
    assert count(runtime, "assessment_participants") == 1
    assert count(runtime, "problems") == 1
    assert count(runtime, "submissions") == 1
    assert count(runtime, "test_observations") == 1
    assert count(runtime, "students") == 1


def test_scores_and_observations_are_stored_in_hundredths(runtime):
    create(runtime, one_student())

    with runtime.db.transaction() as connection:
        submission = connection.execute("SELECT * FROM submissions").fetchone()
        observation = connection.execute("SELECT * FROM test_observations").fetchone()

    assert submission["language"] == "GXX"
    assert submission["verdict"] == "答案正确"
    assert submission["score_units"] == 500
    assert submission["score_total_units"] == 500
    assert observation["test_index"] == 1
    assert observation["score_units"] == 500
    assert (observation["duration_ms"], observation["memory_kb"]) == (2, 304)


def test_same_file_is_not_imported_twice(runtime):
    data = one_student()

    first = create(runtime, data)
    second = create(runtime, data)

    assert second.duplicate is True
    assert second.assessment_id == first.assessment_id
    assert count(runtime, "assessments") == 1
    assert count(runtime, "submissions") == 1


def test_student_identity_is_reused_across_assessments(runtime):
    create(runtime, one_student())
    # 内容不同（题目号不同）→ 新测评，但学生是同一个人
    create(runtime, package({"正式答卷/S001.html": student_html(label="7-2")}))

    assert count(runtime, "assessments") == 2
    assert count(runtime, "students") == 1
    assert count(runtime, "assessment_participants") == 2


def test_teacher_without_scope_is_forbidden(runtime):
    outsider = runtime.admin.create_teacher("synthetic-outsider")
    outsider_token = runtime.admin.issue_token(outsider, hours=168)
    principal = authenticate(runtime.db, outsider_token)
    data = one_student()

    with pytest.raises(APIError) as error:
        create(runtime, data, principal=principal)

    assert error.value.status_code == 403
    assert count(runtime, "assessments") == 0
