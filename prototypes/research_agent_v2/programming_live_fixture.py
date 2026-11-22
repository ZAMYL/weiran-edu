"""One reproducible synthetic answer sheet. No files or submissions are read as input."""

from io import BytesIO
from zipfile import ZipFile, ZipInfo

FIXTURE_NAME = "live-01-synthetic.zip"


def synthetic_zip() -> bytes:
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        for index in range(15):
            # Two disjoint five-cell failure directions and a five-person control group.
            cohort = index // 5
            verdict = "部分正确" if cohort < 2 else "答案正确"
            score = 5 if cohort < 2 else 10
            cells = []
            for point, failure in enumerate(("答案错误", "运行超时"), 1):
                status = failure if cohort == point - 1 else "答案正确"
                earned = 0 if status == failure else 5
                cells.append(
                    f"<tr><td>{point}</td><td>{status}</td><td>{earned}</td>"
                    "<td>1 ms</td><td>32 KB</td></tr>"
                )
            code = (
                "values = [61302]; print(values[0])",
                "for value in [82490]: print(value)",
                "print(sum([93127]))",
            )[cohort]
            html = (
                "<html><body><div class='box'>"
                f"<div><span>考生</span><span>SYN{index:03} 虚构参评者{index:03}</span></div>"
                "<div><span>试卷总分</span><span>10</span></div>"
                f"<div><span>考生得分</span><span>{score}</span></div></div>"
                "<div class='space-y-8'><div class='flex space-x-2'>"
                "<div class='flex-none font-medium'>synthetic-live-problem</div>"
                "<div class='flex-1 space-y-4'><div><span>编译器: Python</span></div>"
                f"<pre class='box font-mono'>{code}</pre>"
                "<table class='result'><thead><tr><th>测试点</th><th>结果</th><th>测试点得分</th>"
                "<th>耗时</th><th>内存</th></tr></thead><tbody>"
                + "".join(cells)
                + "</tbody></table></div>"
                f"<div class='flex-none min-w-[5rem]'><div>{verdict}</div>"
                f"<span>{score} / 10</span></div></div></div></body></html>"
            )
            member = ZipInfo(f"synthetic-private/正式答卷/answer-{index:03}.html")
            member.create_system = 0
            archive.writestr(member, html.encode())
    return data.getvalue()
