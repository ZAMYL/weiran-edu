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
