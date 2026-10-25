"""Generate only fictional V1 workbooks; never read a teacher's workbook."""

import argparse
import json
import random
import sys
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

# Direct CLI and offline tests use the same locked API environment and template constants.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from app.importing.edu.types import DIMENSIONS  # noqa: E402

CASES = ("valid", "warning", "blocked")
MAX_STUDENTS = (10_000 - 1) // 6
CODES = tuple(spec.code for spec in DIMENSIONS)


@dataclass(frozen=True, repr=False)
class Fixture:
    students: int
    seed: int
    case: str
    scores: tuple[tuple[Decimal, ...], ...] = field(repr=False)
    data: bytes = field(repr=False)

    @property
    def filename(self) -> str:
        return f"edu-v1-{self.case}-n{self.students}-seed{self.seed}.xlsx"


def make_fixture(students: int, seed: int, case: str = "valid") -> Fixture:
    if type(students) is not int or not 1 <= students <= MAX_STUDENTS:
        raise ValueError("INVALID_STUDENT_COUNT")
    if type(seed) is not int or case not in CASES:
        raise ValueError("INVALID_FIXTURE_CONFIG")
    randomizer = random.Random(seed)
    scores = [[Decimal(randomizer.randint(100, 500)) / 100 for _ in CODES] for _ in range(students)]
    # Guaranteed neighbors of 2.75 and both scale endpoints; remaining scores vary by seed.
    for index, (algorithm, strategy) in enumerate(
        (("2.74", "4.00"), ("2.75", "4.25"), ("2.76", "4.50"), ("1.00", "1.00"), ("5.00", "5.00"))
    ):
        if index < students:
            scores[index][0] = Decimal(algorithm)
            scores[index][4] = Decimal(strategy)
    if students == 1:
        scores[0][0] = Decimal("2.75")
    if case == "blocked":
        scores[0][0], scores[0][1] = Decimal("5.01"), Decimal("2.751")

    workbook = Workbook()
    rubric = workbook.active
    rubric.title = "Sheet1"
    sheet = workbook.create_sheet("Sheet2")
    for spec in DIMENSIONS:
        rubric[spec.title_cell] = spec.rubric_title
        rubric.merge_cells(spec.rubric_range)
        rubric[spec.rubric_range.split(":")[0]] = f"纯合成依据 {spec.code} seed={seed}"
    sheet.append(["纯合成测评，仅用于本地开发验收"])
    for index, values in enumerate(scores):
        sheet.append([f"{index + 1:012d} 合成占位{index + 1}", "维度", "评分（1~5）", "评语"])
        for dimension, (spec, score) in enumerate(zip(DIMENSIONS, values, strict=True)):
            comment = f"纯合成评语 seed={seed} row={index} dimension={dimension}"
            if case == "warning" and index == 0:
                if dimension == 0:
                    comment = None
                elif dimension in (1, 2):
                    comment = "纯合成重复评语"
            sheet.append([None, spec.label, score, comment])
            sheet.cell(sheet.max_row, 3).number_format = "0.00"
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return Fixture(students, seed, case, tuple(tuple(row) for row in scores), stream.getvalue())


def safe_directory(path: Path) -> Path:
    """Require an explicit existing subdirectory of OS temp, outside every Git checkout."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("OUTPUT_DIRECTORY_UNSAFE")
    resolved = path.resolve(strict=True)
    temporary = Path(tempfile.gettempdir()).resolve()
    if not resolved.is_dir() or resolved == temporary or not resolved.is_relative_to(temporary):
        raise ValueError("OUTPUT_DIRECTORY_UNSAFE")
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction() or (parent / ".git").exists():
            raise ValueError("OUTPUT_DIRECTORY_UNSAFE")
    return resolved


def write_new(directory: Path, filename: str, data: bytes) -> Path:
    directory = safe_directory(directory)
    if Path(filename).name != filename or "/" in filename or "\\" in filename or ":" in filename:
        raise ValueError("OUTPUT_FILENAME_UNSAFE")
    target = directory / filename
    # Exclusive creation also rejects a pre-existing symlink. Never replace user files.
    with target.open("xb") as output:
        output.write(data)
    return target


def fixture_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--students", type=int, default=53)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--case", choices=CASES, default="valid")
    parser.add_argument("--output-dir", type=Path, required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    fixture_arguments(parser)
    args = parser.parse_args(argv)
    try:
        safe_directory(args.output_dir)
        fixture = make_fixture(args.students, args.seed, args.case)
        write_new(args.output_dir, fixture.filename, fixture.data)
    except (OSError, ValueError):
        print(json.dumps({"passed": False, "failure_code": "FIXTURE_OUTPUT_FAILED"}))
        return 2
    print(
        json.dumps(
            {
                "passed": True,
                "students": fixture.students,
                "case": fixture.case,
                "seed": fixture.seed,
                "file": fixture.filename,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
