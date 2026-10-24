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
