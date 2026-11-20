"""B-03 合成答卷样本：只构造数据，不含任何真实学生资料。"""

from io import BytesIO
from zipfile import ZipFile


def paper_html(
    *,
    key: str = "S001",
    name: str = "周同学",
    label: str = "7-1",
    verdict: str = "答案正确",
    ratio: str = "5 / 5",
    rows: list[tuple[str, str, str, str, str]] | None = None,
    code: str = "int main(){ return 0; }",
    started_at: str = "2026-11-01 09:00:00",
    finished_at: str = "2026-11-01 10:30:00",
    paper_type: str | None = None,
) -> str:
    """一份网页答卷。rows 的每一项是 (序号, 状态, 得分, 耗时, 内存)。"""
    if rows is None:
        rows = [("1", "答案正确", "5", "2.00 ms", "304 KB")]
    body = "".join(
        f"<tr><td>{index}</td><td>{status}</td><td>{score}</td><td>{duration}</td><td>{memory}</td></tr>"
        for index, status, score, duration, memory in rows
    )
    paper_type_row = (
        "" if paper_type is None else f"<div><span>答卷类型</span><span>{paper_type}</span></div>"
    )
    return (
        "<html><body><div class='box'>"
        f"<div><span>考生</span><span>{key} {name}</span></div>"
        "<div><span>试卷总分</span><span>235</span></div>"
        "<div><span>考生得分</span><span>76</span></div>"
        f"{paper_type_row}"
        f"<div><span>开始时间</span><span>{started_at}</span></div>"
        f"<div><span>结束时间</span><span>{finished_at}</span></div>"
        "</div>"
        "<div class='space-y-8'><div class='flex space-x-2'>"
        f"<div class='flex-none font-medium'>{label}</div>"
        "<div class='flex-1 space-y-4'>"
        "<div><span>编译器: GXX</span></div>"
        f"<pre class='box font-mono'>{code}</pre>"
        f"<table class='result'><tbody>{body}</tbody></table>"
        "</div>"
        f"<div class='flex-none min-w-[5rem]'><div>{verdict}</div><span>{ratio}</span></div>"
        "</div></div></body></html>"
    )


def zip_of(entries: dict[str, str]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for entry, html in entries.items():
            archive.writestr(entry, html)
    return stream.getvalue()


def simple_zip(**kwargs: object) -> bytes:
    return zip_of({"包/正式答卷/S001.html": paper_html(**kwargs)})
