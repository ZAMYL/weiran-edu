"""Generate only synthetic dual-Sheet workbooks in the test's temporary directory."""
import argparse
from pathlib import Path

from openpyxl import Workbook
from app.importing.edu.types import DIMENSIONS


def make(destination: Path, kind: str) -> None:
    book = Workbook()
    rubric = book.active
    rubric.title = "Sheet1"
    sheet = book.create_sheet("Sheet2")
    for spec in DIMENSIONS:
        rubric[spec.title_cell] = spec.rubric_title
        rubric.merge_cells(spec.rubric_range)
        rubric[spec.rubric_range.split(":")[0]] = "合成评分依据，仅供自动测试"
    sheet.append([])
    size = 401 if kind == "large" else 23
    for index in range(size):
        sheet.append([f"{index + 1:012d} 合成姓名{index}", "维度", "评分（1~5）", "评语"])
        scores = [2.75 if index % 2 == 0 else 3.5, 3.25, 2.5 + .25 * (index % 3), 3.75, 4 + .25 * (index % 2)]
        if kind == "large":
            scores = [round(1 + index / 100, 2), 3.25, 2.5, 3.75, round(5 - index / 100, 2)]
        if kind == "blocked" and index == 0:
            scores[:2] = [6, None]
        for offset, spec in enumerate(DIMENSIONS):
            comment = f"合成私密评语 {kind} {index}:{offset}"
            if kind in {"warning", "blocked"} and offset == 4:
                comment = None
            sheet.append([None, spec.label, scores[offset], comment])
    book.save(destination / f"{kind}.xlsx")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for variant in ("normal", "warning", "blocked", "large"):
        make(args.output, variant)
