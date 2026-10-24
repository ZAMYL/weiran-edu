"""Private parse objects must never be serialized directly to an API or log."""

from dataclasses import dataclass, field

PARSER_VERSION = "current_template.v1"


class ImportFileError(ValueError):
    """Only a fixed, input-independent code crosses this boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ParseLimits:
    compressed_bytes: int = 10 * 1024 * 1024
    expanded_bytes: int = 50 * 1024 * 1024
    zip_entries: int = 256
    rows_per_sheet: int = 10_000
    columns_per_sheet: int = 32
    cells_per_sheet: int = 100_000
    xml_elements_per_part: int = 300_000
    seconds: float = 10.0


@dataclass(frozen=True)
class DimensionSpec:
    code: str
    label: str
    rubric_title: str
    title_cell: str
    rubric_range: str


DIMENSIONS = (
    DimensionSpec(
        "algorithm_data_structure", "算法与数据结构", "算法与数据结构掌握程度", "B2", "L3:P6"
    ),
    DimensionSpec("code_implementation", "代码实现能力", "代码实现能力与工程素养", "C2", "L8:P10"),
    DimensionSpec("problem_modeling", "问题建模能力", "问题分析与建模能力", "D2", "L12:P14"),
    DimensionSpec("efficiency_awareness", "效率意识", "时间与空间效率意识", "E2", "L16:P18"),
    DimensionSpec("solution_strategy", "解题策略", "解题策略与应变能力", "F2", "L20:P22"),
)


@dataclass(frozen=True)
class Source:
    file_sha256: str
    sheet_name: str
    row_number: int
    dimension_cell: str
    score_cell: str
    comment_cell: str


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    sheet_name: str
    cell: str
    row_number: int


@dataclass(frozen=True, repr=False)
class Rubric:
    dimension_code: str
    title: str
    title_cell: str
    source_range: str
    text_cells: tuple[tuple[str, str], ...]


@dataclass(frozen=True, repr=False)
class Evaluation:
    dimension_code: str | None
    score_units: int | None
    # Original XML numeric lexeme or original typed value, including invalid input.
    score_raw: object
    comment_raw: str | None
    source: Source


@dataclass(frozen=True, repr=False)
class StudentBlock:
    preview_student_id: str
    display_label: str
    raw_identity: object
    student_number: str | None
    name: str | None
    header_row: int
    evaluations: tuple[Evaluation, ...]
    complete: bool


@dataclass(frozen=True, repr=False)
class ParsedWorkbook:
    file_sha256: str
    content_sha256: str | None
    students: tuple[StudentBlock, ...] = field(repr=False)
    rubrics: tuple[Rubric, ...] = field(repr=False)
    issues: tuple[Issue, ...]

    @property
    def can_commit(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)
