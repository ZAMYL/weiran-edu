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
    label: str
    ordinal: int
    total_score: int | None


@dataclass(frozen=True)
class SubmissionRecord:
    participant_key: str
    problem_label: str
    language: str | None
    code: str
    verdict: str | None
    score: int | None
    score_total: int | None
    source_entry: str


@dataclass(frozen=True)
class ObservationRecord:
    participant_key: str
    problem_label: str
    test_index: int
    status: str
    score: int | None
    duration_ms: int | None
    memory_kb: int | None


@dataclass(frozen=True)
class AnswerSheetEvidence:
    students: tuple[StudentRecord, ...]
    problems: tuple[ProblemRecord, ...]
    submissions: tuple[SubmissionRecord, ...]
    observations: tuple[ObservationRecord, ...]
    skipped: tuple[tuple[str, str], ...]


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


def parse_answersheet(data: bytes, *, group: str = STUDENT_GROUP) -> AnswerSheetEvidence:
    """解析整包：同一批里的重复学号视为错误，绝不静默覆盖。"""
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
                if f"/{group}/" not in name and not name.startswith(group + "/"):
                    skipped.append((name, "NOT_STUDENT_GROUP"))
                    continue
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
    problems: dict[str, ProblemRecord] = {}

    for name, document in parsed:
        if document["paper_type"] is not None:
            skipped.append((name, "REFERENCE_PAPER"))
            continue
        student = document["student"]
        if student is None:
            skipped.append((name, "PARTICIPANT_MISSING"))
            continue
        if student.participant_key in students:
            raise ImportFileError("ANSWERSHEET_DUPLICATE_PARTICIPANT")
        if len(students) >= MAX_STUDENTS:
            raise ImportFileError("ANSWERSHEET_TOO_MANY_STUDENTS")
        students[student.participant_key] = student

        for problem, submission, rows in document["problems"]:
            problems.setdefault(problem.label, problem)
            submissions.append(submission)
            observations.extend(rows)

    if not students:
        raise ImportFileError("ANSWERSHEET_NO_STUDENT")

    ordered_problems = tuple(sorted(problems.values(), key=lambda item: item.ordinal))
    return AnswerSheetEvidence(
        students=tuple(students.values()),
        problems=ordered_problems,
        submissions=tuple(submissions),
        observations=tuple(observations),
        skipped=tuple(skipped),
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
    for ordinal, block in enumerate(_problem_blocks(root)):
        problems.append(_parse_problem(block, ordinal, student, entry))

    return {"paper_type": paper_type, "student": student, "problems": problems}


def _header_fields(root: _Node) -> dict[str, str]:
    """页头是「加粗标签 span + 值 span」成对出现，按出现顺序收集。"""
    fields: dict[str, str] = {}
    for node in _nodes(root, tag="span"):
        label = _text(node).rstrip(":：")
        if not label:
            continue
        parent = _parent_of(root, node)
        if parent is None or len(parent.children) < 2:
            continue
        value_node = None
        for sibling in parent.children:
            if isinstance(sibling, _Node) and sibling is not node and sibling.tag == "span":
                value_node = sibling
        if value_node is None:
            continue
        key = _HEADER_LABELS.get(label)
        if key:
            fields.setdefault(key, _text(value_node))
    return fields


def _parent_of(root: _Node, target: _Node) -> _Node | None:
    for node in _walk(root):
        for child in node.children:
            if child is target:
                return node
    return None


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

    code = ""
    language = None
    verdict = None
    score = None
    score_total = None
    rows: list[ObservationRecord] = []

    if body is not None:
        pres = [node for node in _nodes(body, tag="pre")]
        if pres:
            code = _raw_text(pres[0]).strip()[:MAX_CODE_CHARS]
        language = _language_of(body)
        table = next(_nodes(body, tag="table"), None)
        if table is not None:
            rows = _observations(table, student, label)
    verdict, score, score_total = _verdict(block)

    problem = ProblemRecord(
        label=label,
        ordinal=ordinal,
        total_score=score_total,
    )
    submission = SubmissionRecord(
        participant_key=student.participant_key if student else "",
        problem_label=label,
        language=language,
        code=code,
        verdict=verdict,
        score=score,
        score_total=score_total,
        source_entry=entry,
    )
    return problem, submission, rows


def _observations(table: _Node, student, label: str) -> list[ObservationRecord]:
    rows: list[ObservationRecord] = []
    body = next(_nodes(table, tag="tbody"), table)
    for row in _nodes(body, tag="tr"):
        cells = [_text(cell) for cell in row.children if isinstance(cell, _Node)]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        index = int(cells[0])
        if index > MAX_OBSERVATIONS_PER_SUBMISSION:
            continue
        rows.append(
            ObservationRecord(
                participant_key=student.participant_key if student else "",
                problem_label=label,
                test_index=index,
                status=cells[1],
                score=_int_or_none(cells[2]),
                duration_ms=_duration_ms(cells[3]),
                memory_kb=_memory_kb(cells[4]),
            )
        )
    return rows


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
        ratio = re.search(r"(\d+)\s*/\s*(\d+)", text)
        score = int(ratio.group(1)) if ratio else None
        total = int(ratio.group(2)) if ratio else None
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
