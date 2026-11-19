"""解析"一人一份 HTML"的答题卡包，产出与数据库无关的证据中间结构。

只认当前这一种模板：页头是「标签 + 值」的 div 对，每个题目是一个
``div.flex.space-x-2``，内含代码 ``pre``、测试点 ``table.result`` 和结论行。
换平台导出格式时，只需改本文件的选择器常量，不动其它层。
"""

import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from zipfile import BadZipFile, ZipFile

from app.importing.edu.types import ImportFileError

STUDENT_GROUP = "正式答卷"

MAX_STUDENTS = 2000
MAX_PROBLEMS = 100
MAX_OBSERVATIONS_PER_SUBMISSION = 500
MAX_CODE_CHARS = 200_000
MAX_COMPILER_OUTPUT_CHARS = 200_000
MAX_ENTRY_BYTES = 8 * 1024 * 1024

_HEADER_LABELS = {
    "开始时间": "started_at",
    "结束时间": "finished_at",
    "答题时长": "duration_minutes",
    "考生": "participant",
    "试卷总分": "total_score",
    "考生得分": "score",
    "答卷类型": "paper_type",
}

_STATUS_WORDS = (
    "答案正确",
    "答案错误",
    "运行超时",
    "内存超限",
    "运行错误",
    "编译错误",
    "格式错误",
    "部分正确",
    "未作答",
)


@dataclass(frozen=True)
class StudentRecord:
    participant_key: str
    display_name: str
    total_score: int | None
    score: int | None
    started_at: str | None
    finished_at: str | None
    duration_minutes: int | None
    source_entry: str


@dataclass(frozen=True)
class ProblemRecord:
    """题目。

    ``source_index`` 是它在原始文件里首次出现的块序号（可能为 None），
    ``display_ordinal`` 是稳定展示顺序；两者不混用，避免把展示顺序谎称为原始顺序。
    """

    label: str
    source_index: int | None
    display_ordinal: int
    total_score: int | None


# 代码字段的状态：有真实代码 / 占位（未作答）/ 文件里根本没有该字段。
CODE_PRESENT = "PRESENT"
CODE_UNANSWERED = "UNANSWERED"
CODE_MISSING = "MISSING"
CODE_PLACEHOLDERS = frozenset({"无", "-", "—", "/"})


@dataclass(frozen=True)
class SubmissionRecord:
    participant_key: str
    problem_label: str
    language: str | None
    code: str | None
    code_state: str
    code_truncated: bool
    compiler_output: str | None
    compiler_output_truncated: bool
    verdict: str | None
    score: int | None
    score_total: int | None
    source_index: int
    source_entry: str


# B-01 要求区分的资料类别；未识别的给 UNKNOWN，不猜。
CATEGORY_STUDENT = "STUDENT_PAPER"
CATEGORY_STANDARD_ANSWER = "STANDARD_ANSWER"
CATEGORY_BLANK_PAPER = "BLANK_PAPER"
CATEGORY_TEST_DATA = "TEST_DATA"
CATEGORY_PROBLEM_STATEMENT = "PROBLEM_STATEMENT"
CATEGORY_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MaterialRecord:
    """一个源文件、它的角色，以及判定依据。

    ``needs_confirmation`` 为真表示证据不足以自动定论，需要人工确认；
    这类文件不会被静默计入或静默丢弃。
    """

    entry: str
    category: str
    basis: str
    signals: tuple[str, ...] = ()
    needs_confirmation: bool = False


@dataclass(frozen=True)
class Diagnostic:
    """可诊断反馈：哪条数据、什么问题、依据是什么。"""

    code: str
    entry: str | None
    detail: str


@dataclass(frozen=True)
class ObservationRecord:
    participant_key: str
    problem_label: str
    test_index: int
    status: str
    score: int | None
    duration_ms: int | None
    memory_kb: int | None
    source_row: int


# 题目顺序的依据：来自各文件一致的原始顺序，或结构性排序。
ORDER_SOURCE = "SOURCE"
ORDER_NATURAL = "NATURAL_SORT"


@dataclass(frozen=True)
class AnswerSheetEvidence:
    students: tuple[StudentRecord, ...]
    problems: tuple[ProblemRecord, ...]
    submissions: tuple[SubmissionRecord, ...]
    observations: tuple[ObservationRecord, ...]
    materials: tuple[MaterialRecord, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    order_basis: str = ORDER_NATURAL


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple(self.attrs.get("class", "").split())


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].children.append(data)


def _build_tree(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _walk(node: _Node):
    yield node
    for child in node.children:
        if isinstance(child, _Node):
            yield from _walk(child)


def _nodes(node: _Node, *, tag: str | None = None, classes: tuple[str, ...] = ()):
    for candidate in _walk(node):
        if tag is not None and candidate.tag != tag:
            continue
        if classes and not set(classes).issubset(candidate.classes):
            continue
        yield candidate


def _text(node: _Node) -> str:
    parts: list[str] = []

    def visit(current: _Node) -> None:
        for child in current.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                visit(child)

    visit(node)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _raw_text(node: _Node) -> str:
    """代码块必须保留换行，不能像展示文本那样折叠空白。"""
    parts: list[str] = []

    def visit(current: _Node) -> None:
        for child in current.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                visit(child)

    visit(node)
    return "".join(parts)


def _own_text(node: _Node) -> str:
    """只看节点自己的文本，不吃子节点内容（用于「编译器: GXX」这类标签行）。"""
    return "".join(child for child in node.children if isinstance(child, str)).strip()


def parse_answersheet(data: bytes, *, group: str | None = STUDENT_GROUP) -> AnswerSheetEvidence:
    """解析整包：同一批里的重复学号视为错误，绝不静默覆盖。

    ``group`` 只是目录线索，不单独决定角色；角色由内容与依据共同判断。
    """
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = [
                name
                for name in archive.namelist()
                if name.lower().endswith((".html", ".htm")) and not name.endswith("/")
            ]
            parsed = []
            skipped: list[tuple[str, str]] = []
            for name in entries:
                raw = archive.read(name)
                if len(raw) > MAX_ENTRY_BYTES:
                    skipped.append((name, "ENTRY_TOO_LARGE"))
                    continue
                parsed.append((name, _parse_document(raw, name)))
    except (BadZipFile, KeyError, OSError):
        raise ImportFileError("EXTRACTION_ZIP_UNREADABLE") from None

    students: dict[str, StudentRecord] = {}
    submissions: list[SubmissionRecord] = []
    observations: list[ObservationRecord] = []
    materials: list[MaterialRecord] = []
    diagnostics: list[Diagnostic] = []
    sequences: list[list[str]] = []
    totals: dict[str, int | None] = {}
    source_indexes: dict[str, int | None] = {}

    for name, document in parsed:
        material = _category_of(name, document, group)
        materials.append(material)
        diagnostics.extend(document["diagnostics"])
        if material.needs_confirmation:
            diagnostics.append(
                Diagnostic(
                    code="MATERIAL_ROLE_UNCONFIRMED",
                    entry=name,
                    detail=f"依据：{material.basis}；线索：{'/'.join(material.signals) or '无'}",
                )
            )
        if material.category != CATEGORY_STUDENT:
            skipped.append((name, material.category))
            continue

        student = document["student"]
        if student is None:
            skipped.append((name, "PARTICIPANT_MISSING"))
            diagnostics.append(
                Diagnostic("PARTICIPANT_MISSING", name, "分类为学生答卷但没有考生字段")
            )
            continue
        if student.participant_key in students:
            raise ImportFileError("ANSWERSHEET_DUPLICATE_PARTICIPANT")
        if len(students) >= MAX_STUDENTS:
            raise ImportFileError("ANSWERSHEET_TOO_MANY_STUDENTS")
        students[student.participant_key] = student

        sequences.append([problem.label for problem, _, _, _ in document["problems"]])
        for problem, submission, rows, extra in document["problems"]:
            totals.setdefault(problem.label, problem.total_score)
            source_indexes.setdefault(problem.label, problem.source_index)
            submissions.append(submission)
            observations.extend(rows)
            diagnostics.extend(extra)

    if not students:
        raise ImportFileError("ANSWERSHEET_NO_STUDENT")

    ordered, basis = _order_problems(sequences, totals, source_indexes)
    return AnswerSheetEvidence(
        students=tuple(students.values()),
        problems=ordered,
        submissions=tuple(submissions),
        observations=tuple(observations),
        materials=tuple(materials),
        skipped=tuple(skipped),
        diagnostics=tuple(diagnostics),
        order_basis=basis,
    )


def _natural_key(label: str) -> tuple:
    """7-2 排在 7-10 前面；非数字段按字符串比较。"""
    parts = re.split(r"(\d+)", label)
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts if part)


def _order_problems(
    sequences: list[list[str]],
    totals: dict[str, int | None],
    source_indexes: dict[str, int | None],
) -> tuple[tuple[ProblemRecord, ...], str]:
    """决定题目展示顺序。

    仅当所有文件给出的题序完全一致时，才声称这是原始顺序；否则用稳定的自然排序，
    并如实标注依据。
    """
    first_seen: dict[str, int] = {}
    for sequence in sequences:
        for label in sequence:
            first_seen.setdefault(label, len(first_seen))
    agreed = bool(sequences) and len({tuple(sequence) for sequence in sequences}) == 1
    if agreed:
        order = list(sequences[0])
        basis = ORDER_SOURCE
    else:
        order = sorted(first_seen, key=lambda label: (_natural_key(label), first_seen[label]))
        basis = ORDER_NATURAL
    return (
        tuple(
            ProblemRecord(
                label=label,
                source_index=source_indexes.get(label),
                display_ordinal=index,
                total_score=totals.get(label),
            )
            for index, label in enumerate(order)
        ),
        basis,
    )


def merge_answersheets(documents: list[AnswerSheetEvidence]) -> AnswerSheetEvidence:
    """把多份答题卡包的证据合成一份；跨包重复的学号或题目直接报错。

    多文件上传时每份文件单独解析，这里只做"合并 + 冲突拒绝"，
    不改变任何单包内的解析规则。
    """
    if not documents:
        raise ImportFileError("ANSWERSHEET_NO_STUDENT")

    students: dict[str, StudentRecord] = {}
    submissions: list[SubmissionRecord] = []
    observations: list[ObservationRecord] = []
    seen_submissions: set[tuple[str, str]] = set()
    materials: list[MaterialRecord] = []
    skipped: list[tuple[str, str]] = []
    diagnostics: list[Diagnostic] = []
    sequences: list[list[str]] = []
    totals: dict[str, int | None] = {}
    source_indexes: dict[str, int | None] = {}

    for document in documents:
        materials.extend(document.materials)
        skipped.extend(document.skipped)
        diagnostics.extend(document.diagnostics)
        sequences.append([problem.label for problem in document.problems])
        for problem in document.problems:
            totals.setdefault(problem.label, problem.total_score)
            source_indexes.setdefault(problem.label, problem.source_index)
        for student in document.students:
            if student.participant_key in students:
                raise ImportFileError("ANSWERSHEET_DUPLICATE_PARTICIPANT")
            students[student.participant_key] = student
        for submission in document.submissions:
            marker = (submission.participant_key, submission.problem_label)
            if marker in seen_submissions:
                raise ImportFileError("ANSWERSHEET_DUPLICATE_SUBMISSION")
            seen_submissions.add(marker)
            submissions.append(submission)
        observations.extend(document.observations)

    if not students:
        raise ImportFileError("ANSWERSHEET_NO_STUDENT")

    ordered, basis = _order_problems(sequences, totals, source_indexes)
    return AnswerSheetEvidence(
        students=tuple(students.values()),
        problems=ordered,
        submissions=tuple(submissions),
        observations=tuple(observations),
        materials=tuple(materials),
        skipped=tuple(skipped),
        diagnostics=tuple(diagnostics),
        order_basis=basis,
    )


def _category_of(entry: str, document: dict, group: str | None) -> MaterialRecord:
    """综合内容与目录线索判断角色，并记录依据。

    内容（「答卷类型」、考生字段）优先；目录/文件名只是线索，
    单靠路径得出的结论一律标记为"待确认"，不静默计入或丢弃。
    """
    paper_type = document["paper_type"]
    student = document["student"]
    in_group = bool(group) and (f"/{group}/" in entry or entry.startswith(f"{group}/"))

    signals: list[str] = []
    if paper_type:
        signals.append(f"content:paper_type={paper_type}")
    if student is not None:
        signals.append("content:participant")
    if in_group:
        signals.append(f"path:{group}")
    for hint in ("验题", "测试", "题目", "参考"):
        if hint in entry:
            signals.append(f"path:{hint}")

    if paper_type:
        if "空白" in paper_type:
            return MaterialRecord(entry, CATEGORY_BLANK_PAPER, "content:paper_type", tuple(signals))
        if "答案" in paper_type:
            return MaterialRecord(
                entry, CATEGORY_STANDARD_ANSWER, "content:paper_type", tuple(signals)
            )
        return MaterialRecord(
            entry, CATEGORY_UNKNOWN, "content:paper_type", tuple(signals), needs_confirmation=True
        )

    if student is not None:
        # 有身份信息，但路径提示这是验题、测试、题目或参考资料时，
        # 不能擅自纳入正式学生集合：给出类别并标记待确认。
        suspicious = [hint for hint in ("验题", "测试", "题目", "参考") if hint in entry]
        if suspicious:
            category = (
                CATEGORY_TEST_DATA
                if any(hint in entry for hint in ("验题", "测试"))
                else CATEGORY_PROBLEM_STATEMENT
            )
            return MaterialRecord(
                entry, category, "path_hint_over_content", tuple(signals), needs_confirmation=True
            )
        basis = "content:participant" + ("+path" if in_group else "")
        return MaterialRecord(entry, CATEGORY_STUDENT, basis, tuple(signals))

    if any(hint in entry for hint in ("验题", "测试")):
        return MaterialRecord(
            entry, CATEGORY_TEST_DATA, "path_hint", tuple(signals), needs_confirmation=True
        )
    if "题目" in entry:
        return MaterialRecord(
            entry,
            CATEGORY_PROBLEM_STATEMENT,
            "path_hint",
            tuple(signals),
            needs_confirmation=True,
        )
    return MaterialRecord(
        entry, CATEGORY_UNKNOWN, "insufficient_evidence", tuple(signals), needs_confirmation=True
    )


def _parse_document(raw: bytes, entry: str) -> dict:
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            html = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise ImportFileError("ANSWERSHEET_ENCODING_UNKNOWN") from None

    root = _build_tree(html)
    header = _header_fields(root)
    paper_type = header.get("paper_type")
    student = None
    if paper_type is None:
        student = _student_from_header(header, entry)

    problems = []
    diagnostics: list[Diagnostic] = []
    for ordinal, block in enumerate(_problem_blocks(root)):
        problems.append(_parse_problem(block, ordinal, student, entry))

    return {
        "paper_type": paper_type,
        "student": student,
        "problems": problems,
        "diagnostics": diagnostics,
    }


def _header_fields(root: _Node) -> dict[str, str]:
    """页头是「加粗标签 span + 值 span」成对出现，按出现顺序收集。

    一次遍历带父节点，避免对每个 span 再全树找父节点（那是 O(n²)）。
    """
    fields: dict[str, str] = {}

    def visit(node: _Node, parent: _Node | None) -> None:
        if node.tag == "span" and parent is not None:
            label = _text(node).rstrip(":：")
            if label and len(parent.children) >= 2:
                value_node = None
                for sibling in parent.children:
                    if isinstance(sibling, _Node) and sibling is not node and sibling.tag == "span":
                        value_node = sibling
                key = _HEADER_LABELS.get(label)
                if key and value_node is not None:
                    fields.setdefault(key, _text(value_node))
        for child in node.children:
            if isinstance(child, _Node):
                visit(child, node)

    visit(root, None)
    return fields


def _student_from_header(header: dict[str, str], entry: str) -> StudentRecord | None:
    participant = header.get("participant")
    if not participant:
        return None
    match = re.match(r"^\s*([A-Za-z0-9_-]+)\s*(.*)$", participant)
    if match is None:
        return None
    key, name = match.group(1), match.group(2).strip()
    return StudentRecord(
        participant_key=key,
        display_name=name,
        total_score=_int_or_none(header.get("total_score")),
        score=_int_or_none(header.get("score")),
        started_at=header.get("started_at"),
        finished_at=header.get("finished_at"),
        duration_minutes=_int_or_none(
            (header.get("duration_minutes") or "").removesuffix("分钟").strip()
        ),
        source_entry=entry,
    )


def _problem_blocks(root: _Node) -> list[_Node]:
    blocks = [
        node
        for node in _nodes(root, tag="div", classes=("flex", "space-x-2"))
        if any(isinstance(child, _Node) and "flex-none" in child.classes for child in node.children)
    ]
    return blocks[:MAX_PROBLEMS]


def _parse_problem(block: _Node, ordinal: int, student, entry: str):
    label_node = next(
        (
            child
            for child in block.children
            if isinstance(child, _Node) and "flex-none" in child.classes
        ),
        None,
    )
    label = _text(label_node) if label_node is not None else f"#{ordinal + 1}"
    body = next(
        (child for child in block.children if isinstance(child, _Node) and child is not label_node),
        None,
    )

    code_text: str | None = None
    code_truncated = False
    language = None
    compiler_output: str | None = None
    compiler_truncated = False
    verdict = None
    score = None
    score_total = None
    rows: list[ObservationRecord] = []
    diagnostics: list[Diagnostic] = []

    if body is not None:
        pres = [node for node in _nodes(body, tag="pre")]
        if pres:
            raw_code = _raw_text(pres[0]).strip()
            if len(raw_code) > MAX_CODE_CHARS:
                raw_code = raw_code[:MAX_CODE_CHARS]
                code_truncated = True
            code_text = raw_code
        language = _language_of(body)
        compiler_output, compiler_truncated = _compiler_output(body)
        table = next(_nodes(body, tag="table"), None)
        if table is not None:
            rows, row_diagnostics = _observations(table, student, label, ordinal, entry)
            diagnostics.extend(row_diagnostics)
    verdict, score, score_total = _verdict(block)

    code, code_state = _code_state(code_text, verdict)
    if code_truncated:
        diagnostics.append(
            Diagnostic("CODE_TRUNCATED", entry, f"题块 {label} 代码超过 {MAX_CODE_CHARS} 字符")
        )
    if compiler_truncated:
        diagnostics.append(
            Diagnostic(
                "COMPILER_OUTPUT_TRUNCATED",
                entry,
                f"题块 {label} 编译日志超过 {MAX_COMPILER_OUTPUT_CHARS} 字符",
            )
        )

    problem = ProblemRecord(
        label=label,
        source_index=ordinal,
        display_ordinal=ordinal,
        total_score=score_total,
    )
    submission = SubmissionRecord(
        participant_key=student.participant_key if student else "",
        problem_label=label,
        language=language,
        code=code,
        code_state=code_state,
        code_truncated=code_truncated,
        compiler_output=compiler_output,
        compiler_output_truncated=compiler_truncated,
        verdict=verdict,
        score=score,
        score_total=score_total,
        source_index=ordinal,
        source_entry=entry,
    )
    return problem, submission, rows, diagnostics


def _code_state(code_text: str | None, verdict: str | None) -> tuple[str | None, str]:
    """区分真实代码、占位符与缺失；占位符不当作有效代码。"""
    if code_text is None:
        return None, CODE_MISSING
    if code_text in CODE_PLACEHOLDERS:
        return None, CODE_UNANSWERED
    if verdict == "未作答":
        return None, CODE_UNANSWERED
    return code_text, CODE_PRESENT


def _observations(
    table: _Node, student, label: str, block_index: int, entry: str
) -> tuple[list[ObservationRecord], list[Diagnostic]]:
    rows: list[ObservationRecord] = []
    diagnostics: list[Diagnostic] = []
    body = next(_nodes(table, tag="tbody"), table)
    tr_nodes = [row for row in _nodes(body, tag="tr")]
    matrix = [[_text(cell) for cell in row.children if isinstance(cell, _Node)] for row in tr_nodes]
    columns, basis = _infer_columns(table, matrix)
    if basis != "thead":
        diagnostics.append(
            Diagnostic(
                "TEST_COLUMNS_INFERRED",
                entry,
                f"题块 {label} 测试表按{basis}识别列序：{columns}",
            )
        )

    for row_position, cells in enumerate(matrix):
        index_col = columns["index"]
        if len(cells) <= index_col or not cells[index_col].strip().isdigit():
            if cells and any(cell.strip() for cell in cells):
                diagnostics.append(
                    Diagnostic(
                        "TEST_ROW_UNRECOGNISED",
                        entry,
                        f"题块 {label} 第 {row_position + 1} 行无法识别测试点序号",
                    )
                )
            continue
        index = int(cells[index_col].strip())
        if index > MAX_OBSERVATIONS_PER_SUBMISSION:
            continue

        rows.append(
            ObservationRecord(
                participant_key=student.participant_key if student else "",
                problem_label=label,
                test_index=index,
                status=_cell(cells, columns["status"]).strip(),
                score=_int_or_none(_cell(cells, columns["score"])),
                duration_ms=_duration_ms(_cell(cells, columns["duration"])),
                memory_kb=_memory_kb(_cell(cells, columns["memory"])),
                source_row=row_position,
            )
        )
    return rows, diagnostics


def _cell(cells: list[str], column: int) -> str:
    return cells[column] if column < len(cells) else ""


DEFAULT_COLUMNS = {"index": 0, "status": 1, "score": 2, "duration": 3, "memory": 4}
_HEADER_KEYS = (
    ("测试点", "index"),
    ("结果", "status"),
    ("测试点得分", "score"),
    ("耗时", "duration"),
    ("内存", "memory"),
)
_DURATION_HINT = re.compile(r"(ms|毫秒|s$|秒)", re.I)
_MEMORY_HINT = re.compile(r"(KB|MB|字节)", re.I)


def _infer_columns(table: _Node, matrix: list[list[str]]) -> tuple[dict[str, int], str]:
    """识别测试表的列含义：表头优先，其次按单元格内容特征，最后退回位置约定。"""
    named = _column_indexes(table)
    mapping = {key: named[name] for name, key in _HEADER_KEYS if name in named}
    if len(mapping) == len(_HEADER_KEYS):
        return mapping, "thead"

    width = max((len(row) for row in matrix), default=0)
    if width < len(_HEADER_KEYS):
        return {**DEFAULT_COLUMNS, **mapping}, "position"

    samples = [row for row in matrix if len(row) >= width and any(cell.strip() for cell in row)]
    samples = samples[:100]
    if not samples:
        return {**DEFAULT_COLUMNS, **mapping}, "position"

    def rate(column: int, predicate) -> float:
        return sum(1 for row in samples if predicate(row[column].strip())) / len(samples)

    index_column = next(
        (
            column
            for column in range(width)
            if all(
                row[column].strip().isdigit() and int(row[column]) == position + 1
                for position, row in enumerate(samples)
            )
        ),
        None,
    )
    status_column = max(
        range(width), key=lambda c: rate(c, lambda v: any(w in v for w in _STATUS_WORDS))
    )
    duration_column = max(
        range(width), key=lambda c: rate(c, lambda v: bool(_DURATION_HINT.search(v)))
    )
    memory_column = max(range(width), key=lambda c: rate(c, lambda v: bool(_MEMORY_HINT.search(v))))

    # 内容特征必须真的命中，否则不算识别成功
    if rate(duration_column, lambda v: bool(_DURATION_HINT.search(v))) < 0.5:
        return {**DEFAULT_COLUMNS, **mapping}, "position"
    if rate(memory_column, lambda v: bool(_MEMORY_HINT.search(v))) < 0.5:
        return {**DEFAULT_COLUMNS, **mapping}, "position"
    if rate(status_column, lambda v: any(w in v for w in _STATUS_WORDS)) < 0.5:
        return {**DEFAULT_COLUMNS, **mapping}, "position"

    inferred = {
        "index": index_column if index_column is not None else DEFAULT_COLUMNS["index"],
        "status": status_column,
        "duration": duration_column,
        "memory": memory_column,
    }
    used = set(inferred.values())
    score_column = next((c for c in range(width) if c not in used), DEFAULT_COLUMNS["score"])
    inferred["score"] = score_column
    return {**inferred, **mapping}, "content_signals"


def _compiler_output(body: _Node) -> tuple[str | None, bool]:
    """「编译器输出」块里的 pre 原文；内容为「无」时视为没有输出。"""
    pre = _pre_after_label(body, "编译器输出")
    if pre is None:
        return None, False
    text = _raw_text(pre).strip()
    if not text or text == "无":
        return None, False
    if len(text) > MAX_COMPILER_OUTPUT_CHARS:
        return text[:MAX_COMPILER_OUTPUT_CHARS], True
    return text, False


def _column_indexes(table: _Node) -> dict[str, int]:
    """读 thead 的列名；没有表头时返回空字典。"""
    head = next(_nodes(table, tag="thead"), None)
    if head is None:
        return {}
    indexes: dict[str, int] = {}
    for index, header in enumerate(_nodes(head, tag="th")):
        name = _text(header).strip()
        if name:
            indexes.setdefault(name, index)
    return indexes


def _pre_after_label(root: _Node, label: str) -> _Node | None:
    """找「标签 div + 内容 div」结构里的 pre。

    结构是 ``<div><div>标签</div><div><pre>内容</pre></div></div>``，
    所以要在 pre 的**祖父**节点上检查第一个子 div 的文本。
    """

    def matches(pre: _Node, ancestors: list[_Node]) -> bool:
        if len(ancestors) < 2:
            return False
        container = ancestors[-2]
        first = next((child for child in container.children if isinstance(child, _Node)), None)
        return (
            first is not None
            and first.tag == "div"
            and first is not pre
            and _own_text(first).strip() == label
        )

    def search(node: _Node, ancestors: list[_Node]) -> _Node | None:
        if node.tag == "pre" and matches(node, ancestors):
            return node
        for child in node.children:
            if isinstance(child, _Node):
                found = search(child, [*ancestors, node])
                if found is not None:
                    return found
        return None

    return search(root, [])


def _language_of(body: _Node) -> str | None:
    """「编译器: GXX」可能直接是 div 文本，也可能被 span 包着。

    外层容器文本也会以「编译器:」开头并连带整段代码，所以取**最短**的匹配节点。
    """
    best: str | None = None
    for node in _nodes(body):
        text = _text(node)
        if text.startswith("编译器:") and (best is None or len(text) < len(best)):
            best = text
    if best is None:
        return None
    return best.split(":", 1)[1].strip() or None


def _verdict(body: _Node) -> tuple[str | None, int | None, int | None]:
    for node in _nodes(body, tag="div"):
        if "min-w-[5rem]" not in node.classes:
            continue
        text = _text(node)
        status = next((word for word in _STATUS_WORDS if word in text), None)
        # 未作答时该行的分数是「- / 20」：保留满分，得分置空而不是当成 0。
        ratio = re.search(r"(-|\d+)\s*/\s*(\d+)", text)
        score = None
        total = None
        if ratio is not None:
            score = int(ratio.group(1)) if ratio.group(1).isdigit() else None
            total = int(ratio.group(2))
        return status, score, total
    return None, None, None


def _int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"-?\d+", value)
    return int(match.group(0)) if match else None


def _duration_ms(value: str) -> int | None:
    match = re.search(r"([\d.]+)\s*ms", value)
    if match is None:
        return None
    return int(round(float(match.group(1))))


def _memory_kb(value: str) -> int | None:
    match = re.search(r"([\d.]+)\s*(KB|MB)", value, re.I)
    if match is None:
        return None
    amount = float(match.group(1))
    return int(round(amount * 1024)) if match.group(2).upper() == "MB" else int(round(amount))
