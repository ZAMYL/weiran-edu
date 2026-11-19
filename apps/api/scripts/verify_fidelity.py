"""代码与编译日志保真核验：独立抽取原始 HTML 文本，与解析结果逐条比对。

    python scripts/verify_fidelity.py <答题卡.zip>

独立一侧只做三件事：按结论行切题块、正则取 ``<pre>``、HTML 实体解码，
不使用解析器的 DOM 与选择器，因此能发现"代码被改写"这类问题。

只输出计数与差异片段（截断），不输出姓名或完整代码。
"""

import html
import re
import sys
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.importing.edu.answersheet import CATEGORY_STUDENT, parse_answersheet  # noqa: E402

BLOCK_SPLIT = "min-w-[5rem]"
PRE_PATTERN = re.compile(r"<pre\b[^>]*>(.*?)</pre>", re.S | re.I)
COMPILER_PATTERN = re.compile(r"编译器输出.*?<pre\b[^>]*>(.*?)</pre>", re.S | re.I)
TAG_PATTERN = re.compile(r"<[^>]+>")


def _plain(fragment: str) -> str:
    return html.unescape(TAG_PATTERN.sub("", fragment)).strip()


def raw_blocks(text: str) -> list[tuple[str, str | None]]:
    """每个题块返回 (代码, 编译输出)；结论行把文件切成若干题块。

    结论行位于每个题块末尾，所以最后一段只是文件尾，不属于任何题块。
    """
    parts = text.split(BLOCK_SPLIT)[:-1]
    blocks: list[tuple[str, str | None]] = []
    for segment in parts:
        code_match = PRE_PATTERN.search(segment)
        code = _plain(code_match.group(1)) if code_match else ""
        compiler_match = COMPILER_PATTERN.search(segment)
        compiler = _plain(compiler_match.group(1)) if compiler_match else ""
        blocks.append((code, None if compiler in ("", "无") else compiler))
    return blocks


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    zip_path = Path(sys.argv[1])
    evidence = parse_answersheet(zip_path.read_bytes())
    student_files = {
        material.entry for material in evidence.materials if material.category == CATEGORY_STUDENT
    }

    by_entry: dict[str, list] = {}
    for submission in evidence.submissions:
        by_entry.setdefault(submission.source_entry, []).append(submission)

    checked = 0
    code_mismatch = 0
    compiler_mismatch = 0
    samples: list[str] = []

    with ZipFile(zip_path) as archive:
        for entry in sorted(student_files):
            text = archive.read(entry).decode("utf-8", "replace")
            blocks = raw_blocks(text)
            submissions = by_entry.get(entry, [])
            if len(blocks) != len(submissions):
                samples.append(
                    f"[块数不一致] {entry}: 原始 {len(blocks)} / 解析 {len(submissions)}"
                )
                continue
            for index, submission in enumerate(submissions):
                checked += 1
                raw_code, raw_output = blocks[index]
                parsed_code = (submission.code or "").strip()
                # 未作答/缺失的题块，原始一侧允许是占位符或空；有代码时必须逐字一致。
                if submission.code_state == "PRESENT":
                    code_ok = raw_code == parsed_code
                else:
                    code_ok = raw_code in ("", "无", "-", "—", "/")
                if not code_ok:
                    code_mismatch += 1
                    if len(samples) < 5:
                        samples.append(
                            f"[代码不一致] {submission.problem_label} ({submission.code_state}): "
                            f"原始 {raw_code[:60]!r} / 解析 {parsed_code.strip()[:60]!r}"
                        )
                if (raw_output or "").strip() != (submission.compiler_output or "").strip():
                    compiler_mismatch += 1
                    if len(samples) < 10:
                        raw_preview = (raw_output or "")[:60]
                        parsed_preview = (submission.compiler_output or "").strip()[:60]
                        samples.append(
                            f"[编译日志不一致] {submission.problem_label}: "
                            f"原始 {raw_preview!r} / 解析 {parsed_preview!r}"
                        )

    print(f"核验学生答卷 {len(student_files)} 个文件、{checked} 条提交")
    print(f"代码不一致 {code_mismatch} 条；编译日志不一致 {compiler_mismatch} 条")
    for line in samples:
        print(" ", line)
    ok = checked > 0 and code_mismatch == 0 and compiler_mismatch == 0
    print(f"\n结论：{'保真核验通过' if ok else '存在不一致，需要修正'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
