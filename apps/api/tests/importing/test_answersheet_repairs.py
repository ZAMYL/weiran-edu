"""独立复验发现缺陷的定向回归：角色、代码区域、列歧义、题块冲突。"""

from io import BytesIO
from zipfile import ZipFile, ZipInfo

import pytest

from app.importing.edu.answersheet import parse_answersheet
from app.importing.edu.types import ImportFileError

HEADER = (
    "<thead><tr><th>测试点</th><th>结果</th><th>测试点得分</th><th>耗时</th><th>内存</th>"
    "</tr></thead>"
)
ROW = "<tr><td>1</td><td>答案正确</td><td>5</td><td>2.00 ms</td><td>304 KB</td></tr>"


def block(
    label: str,
    *,
    code: str = "int main(){ return 0; }",
    compiler: str = "无",
    body: str | None = None,
) -> str:
    inner = body or (
        "<div><span>编译器: GXX</span></div>"
        f"<pre class='box'>{code}</pre>"
        "<div style='display:flex'><div>编译器输出</div>"
        f"<div><pre class='box'>{compiler}</pre></div></div>"
        f"<table class='result'>{HEADER}<tbody>{ROW}</tbody></table>"
    )
    return (
        "<div class='flex space-x-2'>"
        f"<div class='flex-none font-medium'>{label}</div>"
        f"<div class='flex-1 space-y-4'>{inner}</div>"
        "<div class='flex-none min-w-[5rem]'><div>答案正确</div><span>5 / 5</span></div>"
        "</div>"
    )


def page(key: str = "S001", name: str = "周同学", blocks: str | None = None) -> str:
    body = blocks if blocks is not None else block("7-1")
    return (
        "<html><body><div class='box'>"
        f"<div><span>考生</span><span>{key} {name}</span></div>"
        "</div>"
        f"<div class='space-y-8'>{body}</div></body></html>"
    )


def zip_bytes(entries: dict[str, str]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), payload)
    return stream.getvalue()


def test_flattened_package_is_refused_instead_of_adding_verify_papers():
    """去掉目录后无法区分正式答卷与验题资料，必须拒绝而不是猜。"""
    package = zip_bytes(
        {
            "a.html": page(),
            "b.html": page(key="T001", name="叶同学"),
        }
    )

    with pytest.raises(ImportFileError) as error:
        parse_answersheet(package)

    assert error.value.code == "ANSWERSHEET_ROLE_UNCONFIRMED"


def test_compiler_output_before_code_is_not_taken_as_code():
    body = (
        "<div style='display:flex'><div>编译器输出</div>"
        "<div><pre class='box'>a.c:1: error: boom</pre></div></div>"
        "<div><span>编译器: GXX</span></div>"
        "<pre class='box'>int real_code() { return 1; }</pre>"
        f"<table class='result'>{HEADER}<tbody>{ROW}</tbody></table>"
    )
    package = zip_bytes({"包/正式答卷/S001.html": page(blocks=block("7-1", body=body))})

    submission = parse_answersheet(package).submissions[0]

    assert submission.code == "int real_code() { return 1; }"
    assert submission.compiler_output == "a.c:1: error: boom"


def test_answered_without_code_area_is_missing_not_unanswered():
    body = (
        "<div><span>编译器: GXX</span></div>"
        "<div style='display:flex'><div>编译器输出</div>"
        "<div><pre class='box'>无</pre></div></div>"
        f"<table class='result'>{HEADER}<tbody>{ROW}</tbody></table>"
    )
    package = zip_bytes({"包/正式答卷/S001.html": page(blocks=block("7-1", body=body))})

    submission = parse_answersheet(package).submissions[0]

    assert submission.code is None
    assert submission.code_state == "MISSING"


def test_ambiguous_columns_do_not_produce_observations():
    body = (
        "<div><span>编译器: GXX</span></div>"
        "<pre class='box'>int main(){}</pre>"
        "<table class='result'><tbody>"
        "<tr><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td></tr>"
        "</tbody></table>"
    )
    package = zip_bytes({"包/正式答卷/S001.html": page(blocks=block("7-1", body=body))})

    evidence = parse_answersheet(package)

    assert evidence.observations == ()
    assert any(d.code == "TEST_COLUMNS_UNCONFIRMED" for d in evidence.diagnostics)


def test_large_test_index_is_kept_not_dropped():
    body = (
        "<div><span>编译器: GXX</span></div>"
        "<pre class='box'>int main(){}</pre>"
        f"<table class='result'>{HEADER}<tbody>"
        "<tr><td>900</td><td>答案正确</td><td>5</td><td>1.50 ms</td><td>64 KB</td></tr>"
        "</tbody></table>"
    )
    package = zip_bytes({"包/正式答卷/S001.html": page(blocks=block("7-1", body=body))})

    observation = parse_answersheet(package).observations[0]

    assert observation.test_index == 900
    assert observation.duration_ms == 1.5


def test_duplicate_identical_block_is_reported_once():
    duplicates = block("7-1") + block("7-1")
    package = zip_bytes({"包/正式答卷/S001.html": page(blocks=duplicates)})

    evidence = parse_answersheet(package)

    assert len(evidence.submissions) == 1
    assert any(d.code == "DUPLICATE_BLOCK_IGNORED" for d in evidence.diagnostics)


def test_conflicting_duplicate_block_is_flagged():
    duplicates = block("7-1") + block("7-1", code="int other(){ return 2; }")
    package = zip_bytes({"包/正式答卷/S001.html": page(blocks=duplicates)})

    evidence = parse_answersheet(package)

    assert len(evidence.submissions) == 1
    assert any(d.code == "DUPLICATE_BLOCK_CONFLICT" for d in evidence.diagnostics)


def test_conflicting_total_score_is_untrusted():
    first = block("7-1").replace("5 / 5", "5 / 10")
    second = block("7-1")
    package = zip_bytes(
        {
            "包/正式答卷/S001.html": page(blocks=first),
            "包/正式答卷/S002.html": page(key="S002", name="吕同学", blocks=second),
        }
    )

    evidence = parse_answersheet(package)

    problem = next(p for p in evidence.problems if p.label == "7-1")
    assert problem.total_score is None
    assert any(d.code == "TOTAL_SCORE_CONFLICT" for d in evidence.diagnostics)


def test_unrecognised_block_structure_is_reported():
    """页面有结论行但题块结构不认识时，不能悄悄少算。"""
    broken = (
        "<div class='flex'>"
        "<div class='flex-none font-medium'>7-1</div>"
        "<div class='flex-1'><pre class='box'>int main(){}</pre></div>"
        "<div class='flex-none min-w-[5rem]'><div>答案正确</div><span>5 / 5</span></div>"
        "</div>"
    )
    package = zip_bytes({"包/正式答卷/S001.html": page(blocks=broken)})

    with pytest.raises(ImportFileError):
        parse_answersheet(package)


def test_unrecognised_block_structure_keeps_students_from_other_files():
    broken = (
        "<div class='flex'>"
        "<div class='flex-none font-medium'>7-1</div>"
        "<div class='flex-1'><pre class='box'>int main(){}</pre></div>"
        "<div class='flex-none min-w-[5rem]'><div>答案正确</div><span>5 / 5</span></div>"
        "</div>"
    )
    package = zip_bytes(
        {
            "包/正式答卷/S001.html": page(),
            "包/正式答卷/S002.html": page(key="S002", name="吕同学", blocks=broken),
        }
    )

    evidence = parse_answersheet(package)

    assert any(
        d.code == "PROBLEM_BLOCKS_UNRECOGNISED" and d.entry.endswith("S002.html")
        for d in evidence.diagnostics
    )


def test_unknown_test_status_is_kept_but_marked_unknown():
    body = (
        "<div><span>编译器: GXX</span></div>"
        "<pre class='box'>int main(){}</pre>"
        f"<table class='result'>{HEADER}<tbody>"
        "<tr><td>1</td><td>评测机抖动</td><td>5</td><td>2.00 ms</td><td>304 KB</td></tr>"
        "</tbody></table>"
    )
    package = zip_bytes({"包/正式答卷/S001.html": page(blocks=block("7-1", body=body))})

    evidence = parse_answersheet(package)
    observation = evidence.observations[0]

    assert observation.status == "评测机抖动"
    assert observation.status_known is False
    assert any(d.code == "TEST_STATUS_UNRECOGNISED" for d in evidence.diagnostics)


def test_same_key_in_student_and_test_group_is_conflicted():
    """同一考号同时出现在正式答卷与验题目录：无法判断真假，置为待确认。"""
    package = zip_bytes(
        {
            "包/正式答卷/T001.html": page(key="T001", name="叶同学"),
            "包/验题测试组/T001-copy.html": page(key="T001", name="叶同学"),
            "包/正式答卷/S001.html": page(),
        }
    )

    evidence = parse_answersheet(package)

    skipped = dict(evidence.skipped)
    assert skipped["包/正式答卷/T001.html"] == "UNKNOWN"
    assert any(d.code == "PARTICIPANT_ROLE_CONFLICT" for d in evidence.diagnostics)
    assert [student.participant_key for student in evidence.students] == ["S001"]


def test_identical_content_in_two_roles_is_reported_not_excluded():
    """答案内容相同的复本只提示可疑，不因此否定学生身份。"""
    copy = page(key="X999", name="复本")
    package = zip_bytes(
        {
            "包/正式答卷/S001.html": page(),
            "包/验题测试组/复本.html": copy,
        }
    )

    evidence = parse_answersheet(package)

    assert [student.participant_key for student in evidence.students] == ["S001"]
    assert any(d.code == "DUPLICATE_CONTENT_ACROSS_ROLES" for d in evidence.diagnostics)


def test_real_official_verdicts_are_recognised():
    """真实导出里的评测结论都要算已知，不能把段错误/非零返回当成未知。"""
    for verdict in ("非零返回", "段错误", "运行时错误", "内存超限"):
        body = (
            "<div><span>编译器: GXX</span></div>"
            "<pre class='box'>int main(){}</pre>"
            f"<table class='result'>{HEADER}<tbody>"
            f"<tr><td>1</td><td>{verdict}</td><td>0</td><td>2.00 ms</td><td>304 KB</td></tr>"
            "</tbody></table>"
        )
        package = zip_bytes({"包/正式答卷/S001.html": page(blocks=block("7-1", body=body))})

        evidence = parse_answersheet(package)

        assert evidence.observations[0].status == verdict
        assert evidence.observations[0].status_known is True
        assert not any(d.code == "TEST_STATUS_UNRECOGNISED" for d in evidence.diagnostics)
