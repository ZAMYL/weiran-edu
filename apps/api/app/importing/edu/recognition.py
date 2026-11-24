"""B-02-S1：统一上传后的资料识别。

只做识别：类型检测 → 结构理解（DS-04 profile/rules）→ 返回角色、字段、待确认项与可用分析。
不落库、不确认字段、不伪造学生或评分。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.ai.edu.material_contract import MaterialInput
from app.ai.edu.material_profile import build_profile
from app.ai.edu.material_rules import rule_suggestions
from app.importing.edu import detect
from app.importing.edu.answersheet import parse_answersheet
from app.importing.edu.extract import extract_document
from app.importing.edu.types import ImportFileError

# 本轮承诺支持的格式；ZIP 走既有正式导入路径，其余先如实报不支持。
STRUCTURED_KINDS = frozenset({detect.XLSX, detect.CSV, detect.HTML})
ZIP_IMPORT_KINDS = frozenset({detect.ZIP_HTML})

_ANALYSES_BY_MEANING = {
    "code": "code_evidence",
    "verdict": "test_evidence",
    "test_result": "test_evidence",
    "test_point": "test_evidence",
    "dimension_score": "five_dimensions",
    "score": "score_evidence",
}


@dataclass(frozen=True)
class RecognizedTable:
    ref: str
    rows: int
    columns: int
    complete: bool
    blocked: bool


@dataclass(frozen=True)
class RecognizedField:
    column: str
    meaning: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class PendingItem:
    code: str
    detail: str


@dataclass(frozen=True)
class Recognition:
    source_id: str
    kind: str
    supported: bool
    role: str | None
    role_state: str
    route: str
    tables: tuple[RecognizedTable, ...] = ()
    fields: tuple[RecognizedField, ...] = ()
    pending: tuple[PendingItem, ...] = ()
    analyses: tuple[str, ...] = ()


def recognize(
    data: bytes,
    *,
    file_name: str | None = None,
    content_type: str | None = None,
) -> Recognition:
    source_id = hashlib.sha256(data).hexdigest()
    asset = detect.detect_asset(data, content_type=content_type)
    kind = asset.kind

    if kind in ZIP_IMPORT_KINDS:
        return _recognize_answersheet(data, source_id, kind)
    if kind in STRUCTURED_KINDS:
        return _recognize_structured(data, source_id, kind)
    if kind == detect.ZIP_OTHER:
        return Recognition(
            source_id=source_id,
            kind=kind,
            supported=False,
            role=None,
            role_state="unsupported",
            route="unsupported",
            pending=(
                PendingItem(
                    "ZIP_CONTENT_UNRECOGNISED",
                    "压缩包内没有可识别的答题卡网页或表格，未进入任何导入路径",
                ),
            ),
        )
    return Recognition(
        source_id=source_id,
        kind=kind,
        supported=False,
        role=None,
        role_state="unsupported",
        route="unsupported",
        pending=(
            PendingItem(
                "FORMAT_UNSUPPORTED",
                f"当前版本不处理 {kind}；支持 XLSX、CSV、HTML 与答题卡 ZIP",
            ),
        ),
    )


def _recognize_structured(data: bytes, source_id: str, kind: str) -> Recognition:
    document = extract_document(kind, data)
    profile = build_profile((MaterialInput(document=document, source_id=source_id),)).safe()
    suggestions = rule_suggestions(profile)

    tables = tuple(
        RecognizedTable(
            ref=table["ref"],
            rows=table["observed_rows"],
            columns=len(table["columns"]),
            complete=bool(table["complete_source_coverage"]),
            blocked=bool(table["blocked"]),
        )
        for table in profile["tables"]
    )
    fields = tuple(
        RecognizedField(
            column=field["column"],
            meaning=field["meaning"],
            evidence=tuple(field["evidence"]),
        )
        for field in suggestions["fields"]
    )
    covered = {field.column for field in fields}
    pending: list[PendingItem] = []
    for table in profile["tables"]:
        for column in table["columns"]:
            if column["ref"] in covered:
                continue
            header = " ".join(column["header_tokens"]) or "(空表头)"
            if column["blocked"] or table["blocked"]:
                pending.append(
                    PendingItem("COLUMN_BLOCKED", f"{column['ref']}（{header}）被上游标记为阻塞")
                )
                continue
            pending.append(
                PendingItem(
                    "FIELD_NEEDS_CONFIRMATION",
                    f"{column['ref']}（{header}）的含义无法由规则确定，需教师确认",
                )
            )
    if document.warnings:
        pending.extend(PendingItem("EXTRACTION_WARNING", text) for text in document.warnings)

    roles = suggestions["roles"]
    role = None
    role_state = "needs_confirmation"
    if len(roles) == 1:
        role = roles[0]["role"]
        role_state = "candidate"
    elif len(roles) > 1:
        role_state = "needs_confirmation"
        pending.append(PendingItem("ROLE_AMBIGUOUS", "同一文件里出现多个候选角色，需教师确认"))
    else:
        pending.append(PendingItem("ROLE_UNCONFIRMED", "规则无法确定资料角色，需教师确认"))

    analyses = sorted(
        {
            _ANALYSES_BY_MEANING[field.meaning]
            for field in fields
            if field.meaning in _ANALYSES_BY_MEANING
        }
    )
    if {"student_key", "problem_key"} <= {field.meaning for field in fields}:
        analyses.append("structure_linkage")

    return Recognition(
        source_id=source_id,
        kind=kind,
        supported=True,
        role=role,
        role_state=role_state,
        route="material_understanding",
        tables=tables,
        fields=fields,
        pending=tuple(pending),
        analyses=tuple(sorted(set(analyses))),
    )


def _recognize_answersheet(data: bytes, source_id: str, kind: str) -> Recognition:
    try:
        evidence = parse_answersheet(data)
    except ImportFileError as error:
        return Recognition(
            source_id=source_id,
            kind=kind,
            supported=True,
            role="programming",
            role_state="needs_confirmation",
            route="assessment_import",
            pending=(PendingItem(str(error.code), "答题卡结构无法自动确认，需教师核对后再导入"),),
        )

    students = len(evidence.students)
    problems = len(evidence.problems)
    pending: list[PendingItem] = []
    for material in evidence.materials:
        if material.needs_confirmation:
            pending.append(
                PendingItem(
                    "MATERIAL_ROLE_UNCONFIRMED",
                    f"{material.entry} 角色依据不足（{material.basis}），需教师确认",
                )
            )
    return Recognition(
        source_id=source_id,
        kind=kind,
        supported=True,
        role="programming",
        role_state="candidate" if students else "needs_confirmation",
        route="assessment_import",
        tables=(
            RecognizedTable(
                ref="answersheet",
                rows=students,
                columns=problems,
                complete=bool(students and problems),
                blocked=False,
            ),
        ),
        pending=tuple(pending),
        analyses=("code_evidence", "test_evidence", "structure_linkage"),
        # 学生/题目计数属于识别结论，不是最终入库结果
        fields=(
            RecognizedField(column="students", meaning="student_key", evidence=("answersheet",)),
            RecognizedField(column="problems", meaning="problem_key", evidence=("answersheet",)),
        ),
    )
