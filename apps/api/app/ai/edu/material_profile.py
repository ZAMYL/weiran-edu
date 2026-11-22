"""Privacy-preserving projection of extracted tables; never opens a source file."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .material_contract import Locator, MaterialInput, TableAuthority

_FORMATS = {"xlsx", "xls", "zip_html", "zip_other", "csv", "html", "pdf", "docx", "txt", "image"}
_NUMERIC = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,3})?")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9:]+(?:\.[0-9]+)?(?:Z|[+-][0-9:]+)?)?")
_DURATION = re.compile(r"[0-9]+(?:\.[0-9]+)?\s*(?:ms|s|min|毫秒|秒|分钟)", re.I)
_MEMORY = re.compile(r"[0-9]+(?:\.[0-9]+)?\s*(?:B|KB|MB|GB|KiB|MiB|GiB|字节)", re.I)
_TOKENS = {
    "student",
    "id",
    "participant",
    "candidate",
    "learner",
    "number",
    "key",
    "problem",
    "question",
    "task",
    "code",
    "source",
    "submission",
    "language",
    "compiler",
    "score",
    "points",
    "grade",
    "mark",
    "verdict",
    "status",
    "test",
    "point",
    "case",
    "result",
    "outcome",
    "runtime",
    "elapsed",
    "memory",
    "started",
    "finished",
    "time",
    "duration",
    "dimension",
    "rating",
    "criterion",
    "feedback",
    "rubric",
    "comment",
    "assessment",
    "evaluation",
    "考生",
    "学号",
    "考号",
    "学生",
    "编号",
    "题目",
    "题号",
    "题标识",
    "程序",
    "源代码",
    "代码",
    "编译器",
    "语言",
    "得分",
    "成绩",
    "评分",
    "分数",
    "判题",
    "结论",
    "状态",
    "测试点",
    "测试结果",
    "结果",
    "用例",
    "耗时",
    "运行时间",
    "内存",
    "开始时间",
    "结束时间",
    "答题时长",
    "维度",
    "维度评分",
    "评价",
    "评语",
    "评分依据",
    "量规",
}


_HEADER_ALIASES = {
    "参赛编号": "考号",
    "小题代码": "题号",
    "程序正文": "源代码",
    "实得分": "得分",
    "oj verdict": "verdict",
}


def _header_tokens(label: str, *, aliases: bool = True) -> list[str]:
    # Reject the entire label unless each token is from the reviewed vocabulary.
    if len(label) > 64 or not re.fullmatch(r"[a-zA-Z\u4e00-\u9fff _-]+", label):
        return []
    normalized = label.strip().lower()
    hint = _HEADER_ALIASES.get(normalized, normalized) if aliases else normalized
    tokens = re.split(r"[ _-]+", hint)
    return tokens if all(token in _TOKENS for token in tokens) else []


def _kind(value: str) -> tuple[str, Decimal | None]:
    if len(value) <= 64 and _NUMERIC.fullmatch(value):
        try:
            number = Decimal(value)
            return ("number", number) if number.is_finite() else ("text", None)
        except InvalidOperation:
            pass
    if len(value) <= 48 and _TIME.fullmatch(value):
        try:
            if len(value) == 10:
                date.fromisoformat(value)
            else:
                datetime.fromisoformat(value)
            return "time", None
        except ValueError:
            pass
    if len(value) <= 64 and _DURATION.fullmatch(value):
        return "duration", None
    if len(value) <= 64 and _MEMORY.fullmatch(value):
        return "memory", None
    return "text", None


@dataclass(frozen=True, repr=False)
class ColumnProfile:
    alias: str
    name_tokens: tuple[str, ...]
    kinds: frozenset[str]
    values: tuple[str, ...] = field(repr=False)
    present_rows: frozenset[int] = field(repr=False)
    missing: int
    distinct: int
    negative: bool
    numeric_range: tuple[Decimal, Decimal] | None
    blank_header: bool
    blocked: bool

    def safe(self) -> dict:
        return {
            "ref": self.alias,
            "header_tokens": list(self.name_tokens),
            "types": sorted(self.kinds),
            "present": len(self.values),
            "missing": self.missing,
            "distinct": self.distinct,
            "blank_header": self.blank_header,
            "blocked": self.blocked,
        }


@dataclass(frozen=True, repr=False)
class TableProfile:
    alias: str
    file: str
    columns: tuple[ColumnProfile, ...]
    rows: int
    complete: bool
    blocked: bool
    authority: TableAuthority


@dataclass(frozen=True, repr=False)
class MaterialProfile:
    files: tuple[dict, ...]
    tables: dict[str, TableProfile]
    columns: dict[str, tuple[TableProfile, ColumnProfile]]
    locators: dict[str, Locator]

    def safe(self) -> dict:
        return {
            "files": self.files,
            "tables": [
                {
                    "ref": t.alias,
                    "file": t.file,
                    "observed_rows": t.rows,
                    "complete_source_coverage": t.complete,
                    "blocked": t.blocked,
                    "columns": [c.safe() for c in t.columns],
                }
                for t in self.tables.values()
            ],
            "sampling": "all_extracted_rows; completeness_depends_on_upstream",
        }


def build_profile(inputs: tuple[MaterialInput, ...]) -> MaterialProfile:
    if (
        not inputs
        or len(inputs) > 24
        or any(not isinstance(i.source_id, str) or not i.source_id.strip() for i in inputs)
        or len({i.source_id for i in inputs}) != len(inputs)
    ):
        raise ValueError("invalid_material_sources")
    files, tables, columns, locators = [], {}, {}, {}
    cell_count = 0
    for file_index, source in enumerate(inputs, 1):
        doc = source.document
        file = f"F{file_index}"
        locators[file] = Locator(source.source_id, None, None)
        if len(tables) + len(doc.tables) > 80 or source.coverage not in {
            "unknown",
            "partial",
            "complete",
        }:
            raise ValueError("material_profile_limit")
        authorities = {a.table_index: a for a in source.authority}
        if len(authorities) != len(source.authority) or any(
            not 0 <= index < len(doc.tables) for index in authorities
        ):
            raise ValueError("invalid_material_authority")
        files.append(
            {
                "ref": file,
                "kind": doc.kind if doc.kind in _FORMATS else "unknown",
                "table_count": len(doc.tables),
                "extracted_text_line_count": len(doc.text_lines),
                "diagnostic_count": len(doc.warnings),
                "coverage": source.coverage,
                "blocked": source.blocked,
            }
        )
        for index, table in enumerate(doc.tables):
            alias = f"{file}.T{index + 1}"
            locators[alias] = Locator(source.source_id, index, table.name)
            declared = authorities.get(index, TableAuthority(index))
            width = max(len(table.header), max(map(len, table.rows), default=0))
            cell_count += len(table.rows) * width
            if len(columns) + width > 360 or cell_count > 2_000_000:
                raise ValueError("material_profile_limit")
            if any(not 0 <= number < width for number, _ in declared.fields) or (
                len({number for number, _ in declared.fields}) != len(declared.fields)
            ):
                raise ValueError("invalid_material_authority")
            field_meanings = dict(declared.fields)
            if len(dict(declared.gates)) != len(declared.gates) or any(
                not proof for _, proof in declared.gates
            ):
                raise ValueError("invalid_material_authority")
            if (
                len({i for i, _, _ in declared.score_ranges}) != len(declared.score_ranges)
                or any(
                    field_meanings.get(i) not in {"score", "dimension_score"}
                    or not low.is_finite()
                    or not high.is_finite()
                    or low > high
                    for i, low, high in declared.score_ranges
                )
                or any(not 0 <= i < width for i in declared.blocked_columns)
            ):
                raise ValueError("invalid_material_authority")
            profiled = []
            for col in range(width):
                ref = f"{alias}.C{col + 1}"
                header = table.header[col] if col < len(table.header) else ""
                locators[ref] = Locator(source.source_id, index, table.name, col, header)
                present = tuple(
                    (number, str(row[col]))
                    for number, row in enumerate(table.rows)
                    if len(row) > col and row[col] is not None and str(row[col]).strip()
                )
                values = tuple(value for _, value in present)
                kinds, negative = set(), False
                numeric_values = []
                for value in values:
                    kind, numeric = _kind(value.strip())
                    kinds.add(kind)
                    negative |= numeric is not None and numeric < 0
                    if numeric is not None:
                        numeric_values.append(numeric)
                profiled.append(
                    ColumnProfile(
                        ref,
                        tuple(_header_tokens(header)),
                        frozenset(kinds),
                        values,
                        frozenset(number for number, _ in present),
                        len(table.rows) - len(values),
                        len(set(values)),
                        negative,
                        (min(numeric_values), max(numeric_values)) if numeric_values else None,
                        not header.strip(),
                        col in declared.blocked_columns,
                    )
                )
            profile = TableProfile(
                alias,
                file,
                tuple(profiled),
                len(table.rows),
                source.coverage == "complete" and not doc.warnings,
                source.blocked or declared.blocked,
                declared,
            )
            tables[alias] = profile
            columns.update((col.alias, (profile, col)) for col in profiled)
    return MaterialProfile(tuple(files), tables, columns, locators)
