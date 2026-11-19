from collections.abc import Callable
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from app.importing.edu.types import DIMENSIONS


@pytest.fixture
def workbook() -> Workbook:
    book = Workbook()
    rubric = book.active
    rubric.title = "Sheet1"
    for spec in DIMENSIONS:
        rubric[spec.title_cell] = spec.rubric_title
        rubric.merge_cells(spec.rubric_range)
        rubric[spec.rubric_range.split(":")[0]] = f"合成评分依据：{spec.label}"
    sheet = book.create_sheet("Sheet2")
    sheet["A1"] = "合成测试数据，不代表真实学生"
    for index in range(2):
        header = 2 + index * 6
        sheet.cell(header, 1, f"{index + 1:012d}{' ' if index == 0 else '-'}合成同学{index}")
        for column, text in enumerate(("维度", "评分（1~5）", "评语"), 2):
            sheet.cell(header, column, text)
        for offset, spec in enumerate(DIMENSIONS, 1):
            row = header + offset
            sheet.cell(row, 2, spec.label)
            sheet.cell(row, 3, (1, 2.75, 3.5, 4.5, 5)[offset - 1])
            sheet.cell(row, 4, f"合成观察 {index}:{offset}")
    return book


@pytest.fixture
def pack() -> Callable[[Workbook], bytes]:
    def save(book: Workbook) -> bytes:
        stream = BytesIO()
        book.save(stream)
        return stream.getvalue()

    return save


def rewrite(data: bytes, member: str, transform: Callable[[bytes], bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(BytesIO(data)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for entry in source.infolist():
            content = source.read(entry.filename)
            target.writestr(
                entry.filename, transform(content) if entry.filename == member else content
            )
    return output.getvalue()


# ---- 合成答题卡包生成器（B-01 结构验证用）----


def _problem_block(label: str, code: str, compiler: str | None, rows: int) -> str:
    compiler_block = (
        "<div style='display:flex'><div style='font-weight:bold'>编译器输出</div>"
        f"<div class='flex-1'><pre class='box font-mono'>{compiler}</pre></div></div>"
        if compiler is not None
        else "<div style='display:flex'><div style='font-weight:bold'>编译器输出</div>"
        "<div class='flex-1'><pre class='box font-mono'>无</pre></div></div>"
    )
    table_rows = "".join(
        f"<tr><td>{index}</td><td>答案正确</td><td>5</td><td>{index}.00 ms</td>"
        f"<td>{100 * index} KB</td></tr>"
        for index in range(1, rows + 1)
    )
    return (
        "<div class='flex space-x-2'>"
        f"<div class='flex-none font-medium'>{label}</div>"
        "<div class='flex-1 space-y-4'>"
        "<div class='space-y-2'><div class='text-xs text-light'>编译器: GXX</div>"
        f"<pre class='box font-mono'>{code}</pre></div>"
        f"{compiler_block}"
        f"<table class='result'><tbody>{table_rows}</tbody></table>"
        "</div>"
        "<div class='text-light flex-none min-w-[5rem] flex justify-end space-x-2'>"
        "<div class='text-sm'>答案正确</div><span>5 / 5</span></div>"
        "</div>"
    )


def student_html(
    *,
    key: str,
    name: str,
    problems: list[str],
    observations_per_problem: int = 2,
    compiler: str | None = None,
) -> str:
    blocks = "".join(
        _problem_block(
            label, f"int main(){{/* {label} */ return 0; }}", compiler, observations_per_problem
        )
        for label in problems
    )
    return (
        "<html><body><div class='box'>"
        "<div><span>开始时间</span><span>11/02/2025 4:26:45 PM</span></div>"
        "<div><span>结束时间</span><span>11/02/2025 5:30:00 PM</span></div>"
        "<div><span>答题时长</span><span>63分钟</span></div>"
        f"<div><span>考生</span><span>{key} {name}</span></div>"
        "<div><span>试卷总分</span><span>100</span></div>"
        "<div><span>考生得分</span><span>80</span></div>"
        f"</div><div class='space-y-8'>{blocks}</div></body></html>"
    )


def reference_html(*, kind: str) -> str:
    """标准答案 / 空白答卷：带「答卷类型」字段，不会被当成学生。"""
    return (
        "<html><body><div class='box'>"
        f"<div><span>答卷类型</span><span>{kind}</span></div>"
        "</div></body></html>"
    )


def build_package(
    *,
    students: int,
    problems: list[str],
    observations_per_problem: int = 2,
    compiler: str | None = None,
    include_reference: bool = False,
    include_verify: bool = False,
) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for index in range(1, students + 1):
            archive.writestr(
                f"包/正式答卷/S{index:03d}-同学{index}.html",
                student_html(
                    key=f"S{index:03d}",
                    name=f"同学{index}",
                    problems=problems,
                    observations_per_problem=observations_per_problem,
                    compiler=compiler,
                ),
            )
        if include_reference:
            archive.writestr("包/参考资料/标准答案.html", reference_html(kind="标准答案"))
            archive.writestr("包/参考资料/空白答卷.html", reference_html(kind="空白答卷"))
        if include_verify:
            archive.writestr(
                "包/验题测试组/T001.html",
                student_html(key="T001", name="叶同学", problems=problems),
            )
    return stream.getvalue()


@pytest.fixture
def make_answersheet() -> Callable[..., bytes]:
    """按人数/题号生成合成答题卡包，供结构验证使用。"""
    return build_package


@pytest.fixture
def make_zip_package() -> Callable[[dict[str, str]], bytes]:
    """把「路径 → HTML」打包成 ZIP，供结构变化测试使用。"""

    def build(entries: dict[str, str]) -> bytes:
        stream = BytesIO()
        with ZipFile(stream, "w") as archive:
            for name, payload in entries.items():
                archive.writestr(name, payload)
        return stream.getvalue()

    return build


@pytest.fixture
def make_student_html() -> Callable[..., str]:
    return student_html
