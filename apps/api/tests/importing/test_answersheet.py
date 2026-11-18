"""答题卡解析器测试：用合成 HTML 覆盖正常、跳过、重复与损坏四类情况。"""

from io import BytesIO
from zipfile import ZipFile

import pytest

from app.importing.edu.answersheet import parse_answersheet
from app.importing.edu.types import ImportFileError


def student_html(
    *,
    key: str = "S001",
    name: str = "周同学",
    problem: str = "7-1",
    code: str = "int main(){ return 0; }",
    verify: bool = False,
) -> str:
    head = (
        "<div class='box'>"
        "<div><span>开始时间</span><span>11/02/2025 4:26:45 PM</span></div>"
        "<div><span>答题时长</span><span>63分钟</span></div>"
        f"<div><span>考生</span><span>{key} {name}</span></div>"
        "<div><span>试卷总分</span><span>235</span></div>"
        "<div><span>考生得分</span><span>76</span></div>"
    )
    if verify:
        head += "<div><span>答卷类型</span><span>标准答案</span></div>"
    head += "</div>"
    body = (
        "<div class='space-y-8'><div class='flex space-x-2'>"
        f"<div class='flex-none font-medium'>{problem}</div>"
        "<div class='flex-1 space-y-4'>"
        "<div class='space-y-2'><div class='text-xs text-light'>编译器: GXX</div>"
        f"<pre class='box text-xs font-mono'>{code}</pre></div>"
        "<div class='space-y-2'><table class='result'><tbody>"
        "<tr><td>1</td><td>答案正确</td><td>5</td><td>2.00 ms</td><td>304 KB</td></tr>"
        "<tr><td>2</td><td>答案错误</td><td>0</td><td>12.50 ms</td><td>1 MB</td></tr>"
        "</tbody></table></div>"
        "</div>"
        "<div class='text-light flex-none min-w-[5rem] flex justify-end space-x-2'>"
        "<div class='text-sm'>答案正确</div><span>5 / 5</span></div>"
        "</div></div>"
    )
    return f"<html><body>{head}{body}</body></html>"


def make_zip(entries: dict[str, str]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return stream.getvalue()


def test_student_is_parsed_completely():
    package = make_zip({"包/正式答卷/S001-周同学.html": student_html()})

    evidence = parse_answersheet(package)

    assert len(evidence.students) == 1
    student = evidence.students[0]
    assert (student.participant_key, student.display_name) == ("S001", "周同学")
    assert (student.score, student.total_score, student.duration_minutes) == (76, 235, 63)

    assert [problem.label for problem in evidence.problems] == ["7-1"]
    submission = evidence.submissions[0]
    assert submission.language == "GXX"
    assert submission.verdict == "答案正确"
    assert (submission.score, submission.score_total) == (5, 5)
    assert submission.code == "int main(){ return 0; }"


def test_code_keeps_line_breaks():
    code = "int main(){\n  return 0;\n}\n"
    package = make_zip({"包/正式答卷/S001.html": student_html(code=code)})

    evidence = parse_answersheet(package)

    # 首尾空白去掉，但行内换行必须保留
    assert evidence.submissions[0].code == "int main(){\n  return 0;\n}"


def test_observations_are_normalised():
    package = make_zip({"包/正式答卷/S001.html": student_html()})

    evidence = parse_answersheet(package)
    first, second = evidence.observations

    assert (first.test_index, first.status, first.score) == (1, "答案正确", 5)
    assert (first.duration_ms, first.memory_kb) == (2, 304)
    assert (second.duration_ms, second.memory_kb) == (12, 1024)


def test_other_groups_are_skipped_with_reason():
    package = make_zip(
        {
            "包/正式答卷/S001.html": student_html(),
            "包/验题测试组/T001.html": student_html(key="T001", name="叶同学"),
            "包/参考资料/标准答案.html": student_html(key="X001", name="答案"),
        }
    )

    evidence = parse_answersheet(package)

    assert [student.participant_key for student in evidence.students] == ["S001"]
    assert {reason for _, reason in evidence.skipped} == {"NOT_STUDENT_GROUP"}


def test_reference_paper_inside_student_group_is_skipped():
    package = make_zip({"包/正式答卷/标准答案.html": student_html(verify=True)})

    with pytest.raises(ImportFileError) as error:
        parse_answersheet(package)

    assert error.value.code == "ANSWERSHEET_NO_STUDENT"


def test_duplicate_participant_is_rejected():
    package = make_zip(
        {
            "包/正式答卷/S001-a.html": student_html(),
            "包/正式答卷/S001-b.html": student_html(),
        }
    )

    with pytest.raises(ImportFileError) as error:
        parse_answersheet(package)

    assert error.value.code == "ANSWERSHEET_DUPLICATE_PARTICIPANT"


def test_no_student_raises_instead_of_empty_result():
    package = make_zip({"包/正式答卷/空.html": "<html><body></body></html>"})

    with pytest.raises(ImportFileError) as error:
        parse_answersheet(package)

    assert error.value.code == "ANSWERSHEET_NO_STUDENT"


def test_broken_zip_raises_unreadable():
    with pytest.raises(ImportFileError) as error:
        parse_answersheet(b"PK\x03\x04broken")

    assert error.value.code == "EXTRACTION_ZIP_UNREADABLE"
