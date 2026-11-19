"""结构变化：重命名、目录重排、文件乱序、题块乱序、无表头。"""

from io import BytesIO
from zipfile import ZipFile, ZipInfo

import pytest

from app.importing.edu.answersheet import (
    CATEGORY_STUDENT,
    ORDER_NATURAL,
    ORDER_SOURCE,
    parse_answersheet,
)
from app.importing.edu.types import ImportFileError

HEADER = (
    "<thead><tr><th>测试点</th><th>结果</th><th>测试点得分</th><th>耗时</th><th>内存</th>"
    "</tr></thead>"
)
ROW = "<tr><td>1</td><td>答案正确</td><td>5</td><td>2.00 ms</td><td>304 KB</td></tr>"


def _block(label: str) -> str:
    return (
        "<div class='flex space-x-2'>"
        f"<div class='flex-none font-medium'>{label}</div>"
        "<div class='flex-1 space-y-4'>"
        "<div><span>编译器: GXX</span></div>"
        "<pre class='box font-mono'>int main(){ return 0; }</pre>"
        f"<table class='result'>{HEADER}<tbody>{ROW}</tbody></table>"
        "</div>"
        "<div class='flex-none min-w-[5rem]'><div>答案正确</div><span>5 / 5</span></div>"
        "</div>"
    )


def student_html(
    *,
    key: str = "S001",
    name: str = "周同学",
    problems: tuple[str, ...] = ("7-1",),
) -> str:
    blocks = "".join(_block(label) for label in problems)
    return (
        "<html><body><div class='box'>"
        f"<div><span>考生</span><span>{key} {name}</span></div>"
        "</div>"
        f"<div class='space-y-8'>{blocks}</div></body></html>"
    )


def zip_bytes(entries: dict[str, str]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), payload)
    return stream.getvalue()


def test_student_role_comes_from_content_and_directory_hint():
    """目录名带学生线索时，内容与路径互相印证即确认；改名不影响结果。"""
    package = zip_bytes({"包/某年级学生答卷/任意文件名.html": student_html()})

    evidence = parse_answersheet(package)

    assert [student.participant_key for student in evidence.students] == ["S001"]
    material = next(m for m in evidence.materials if m.entry.endswith("任意文件名.html"))
    assert material.category == CATEGORY_STUDENT
    assert "content:participant" in material.basis


def test_role_without_evidence_is_refused_not_guessed():
    """无目录线索时不能猜角色：必须显式要求人工确认，而不是静默纳入。"""
    package = zip_bytes({"包/material-001.html": student_html()})

    with pytest.raises(ImportFileError) as error:
        parse_answersheet(package)

    assert error.value.code == "ANSWERSHEET_ROLE_UNCONFIRMED"


def test_file_order_does_not_change_problem_order():
    forward = zip_bytes(
        {
            "包/正式答卷/S001.html": student_html(problems=("7-1", "7-2")),
            "包/正式答卷/S002.html": student_html(
                key="S002", name="吕同学", problems=("7-1", "7-2")
            ),
        }
    )
    backward = zip_bytes(
        {
            "包/正式答卷/S002.html": student_html(
                key="S002", name="吕同学", problems=("7-1", "7-2")
            ),
            "包/正式答卷/S001.html": student_html(problems=("7-1", "7-2")),
        }
    )

    first = parse_answersheet(forward)
    second = parse_answersheet(backward)

    assert [p.label for p in first.problems] == [p.label for p in second.problems]
    assert first.order_basis == second.order_basis == ORDER_SOURCE


def test_conflicting_problem_orders_fall_back_to_stable_order():
    """两份文件题序不一致时，不能声称原始顺序，改用稳定排序。"""
    package = zip_bytes(
        {
            "包/正式答卷/S001.html": student_html(problems=("7-1", "7-2")),
            "包/正式答卷/S002.html": student_html(
                key="S002", name="吕同学", problems=("7-2", "7-1")
            ),
        }
    )

    evidence = parse_answersheet(package)

    assert [p.label for p in evidence.problems] == ["7-1", "7-2"]
    assert [p.display_ordinal for p in evidence.problems] == [0, 1]
    assert evidence.order_basis == ORDER_NATURAL


def test_table_without_header_is_inferred_from_content():
    html = (
        student_html()
        .replace(HEADER, "")
        .replace(
            ROW, "<tr><td>1</td><td>304 KB</td><td>2.00 ms</td><td>答案正确</td><td>5</td></tr>"
        )
    )

    evidence = parse_answersheet(zip_bytes({"包/正式答卷/S001.html": html}))
    observation = evidence.observations[0]

    assert (observation.test_index, observation.status) == (1, "答案正确")
    assert (observation.duration_ms, observation.memory_kb) == (2, 304)
    assert observation.score == 5
    assert any(d.code == "TEST_COLUMNS_INFERRED" for d in evidence.diagnostics)


def test_ambiguous_role_is_reported_for_confirmation():
    package = zip_bytes(
        {
            "包/正式答卷/S001.html": student_html(),
            "包/未知目录/材料.html": "<html><body>没有身份字段</body></html>",
        }
    )

    evidence = parse_answersheet(package)

    material = next(m for m in evidence.materials if m.entry.endswith("材料.html"))
    assert material.needs_confirmation is True
    assert any(
        d.code == "MATERIAL_ROLE_UNCONFIRMED" and d.entry.endswith("材料.html")
        for d in evidence.diagnostics
    )
