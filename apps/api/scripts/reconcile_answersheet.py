"""答题卡 ZIP 的独立对账：用正则数原始 HTML，和解析器结果逐文件比对。

    python scripts/reconcile_answersheet.py <答题卡.zip>

正则计数不依赖解析器的 DOM 结构，因此这是一次独立复核：
- 每个题块的结论行（``min-w-[5rem]``）应有 1 条提交；
- 每个 ``<tr><td>`` 数据行应有 1 条测试点记录。

只输出计数，不输出学生姓名、代码或成绩原文。
"""

import re
import sys
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.importing.edu.answersheet import CATEGORY_STUDENT, parse_answersheet  # noqa: E402

VERDICT_MARKER = "min-w-[5rem]"
ROW_PATTERN = re.compile(r"<tr><td>")


def raw_counts(zip_path: Path) -> dict[str, tuple[int, int]]:
    counts: dict[str, tuple[int, int]] = {}
    with ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith((".html", ".htm")) or name.endswith("/"):
                continue
            text = archive.read(name).decode("utf-8", "replace")
            counts[name] = (text.count(VERDICT_MARKER), len(ROW_PATTERN.findall(text)))
    return counts


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    zip_path = Path(sys.argv[1])
    data = zip_path.read_bytes()
    evidence = parse_answersheet(data)
    raw = raw_counts(zip_path)

    owner = {
        (submission.participant_key, submission.problem_label): submission.source_entry
        for submission in evidence.submissions
    }
    parsed_blocks: Counter[str] = Counter(
        submission.source_entry for submission in evidence.submissions
    )
    parsed_rows: Counter[str] = Counter(
        owner.get((row.participant_key, row.problem_label), "?") for row in evidence.observations
    )

    print(f"文件总数 {len(raw)}")
    print(
        f"解析结果：学生 {len(evidence.students)} / 题目 {len(evidence.problems)} / "
        f"提交 {len(evidence.submissions)} / 测试点 {len(evidence.observations)}"
    )

    # 只对"正式学生答卷"做逐文件对账；标准答案、空白答卷、验题组按类别单独列出。
    student_files = {
        material.entry for material in evidence.materials if material.category == CATEGORY_STUDENT
    }
    excluded = sorted(set(raw) - student_files)

    mismatches = 0
    print(f"\n纳入对账的学生答卷 {len(student_files)} 个；按资料类别排除 {len(excluded)} 个：")
    categories: Counter[str] = Counter(material.category for material in evidence.materials)
    for category, count in sorted(categories.items()):
        if category != CATEGORY_STUDENT:
            print(f"  {category}: {count}")

    print("\n逐文件对账（只列不一致的学生答卷）：")
    for name in sorted(student_files):
        blocks_raw, rows_raw = raw[name]
        blocks = parsed_blocks.get(name, 0)
        rows = parsed_rows.get(name, 0)
        if (blocks_raw, rows_raw) != (blocks, rows):
            mismatches += 1
            print(
                f"  [不一致] {name}: 原始块 {blocks_raw} / 解析 {blocks};"
                f" 原始行 {rows_raw} / 解析 {rows}"
            )

    total_blocks_raw = sum(raw[name][0] for name in student_files)
    total_rows_raw = sum(raw[name][1] for name in student_files)
    print("\n合计：")
    print(f"  题块  原始 {total_blocks_raw} / 解析 {len(evidence.submissions)}")
    print(f"  测试点 原始 {total_rows_raw} / 解析 {len(evidence.observations)}")
    ok = (
        total_blocks_raw == len(evidence.submissions)
        and total_rows_raw == len(evidence.observations)
        and mismatches == 0
    )
    print(f"\n结论：{'对账通过' if ok else '存在差异，需人工核查'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
