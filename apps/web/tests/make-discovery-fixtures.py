"""All identities, comments and scores here are generated synthetic test data."""
import argparse
from pathlib import Path

from openpyxl import Workbook
from app.importing.edu.types import DIMENSIONS


def make(destination: Path, kind: str) -> None:
    book = Workbook()
    rubric = book.active
    rubric.title = 'Sheet1'
    sheet = book.create_sheet('Sheet2')
    for spec in DIMENSIONS:
        rubric[spec.title_cell] = spec.rubric_title
        rubric.merge_cells(spec.rubric_range)
        rubric[spec.rubric_range.split(':')[0]] = '合成私密依据，仅供测试'
    sheet.append([])
    size = {'wide': 53, 'rare': 17, 'empty': 1, 'contrast': 53, 'constant': 17, 'contrast105': 105}[kind]
    for index in range(size):
        values = [(2, 2.75, 3, 4)[index % 4]] * 5
        if kind in ('contrast', 'contrast105'):
            values = [1, 5, 2.75, 3, 4] if index < size // 2 else [5, 1, 2.75, 3, 2]
        if kind == 'constant':
            values = [3, 3, 3, 3, 3]
        if kind == 'rare':
            values = [5, 1, 3, 3, 3] if index == 0 else [3, 3, 3, 3, 3]
        sheet.append([f'{index+1:012d} 合成私密姓名{index}', '维度', '评分（1~5）', '评语'])
        for spec, value in zip(DIMENSIONS, values, strict=True):
            sheet.append([None, spec.label, value, '未审核合成私密评语'])
    book.save(destination / f'{kind}.xlsx')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for variant in ('wide', 'rare', 'empty', 'contrast', 'constant', 'contrast105'):
        make(args.output, variant)
