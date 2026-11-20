"""答题卡证据入库：一个事务写入测评、参与者、题目、提交与测试点。

与 Excel 批次（``batches``）并存：那条链路固定五维评分，
这条链路是"代码 + 测试点"证据，两者共用 ``students`` 作为学生身份。
"""

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.core.errors import APIError
from app.edu.assessment_models import (
    AssessmentOverview,
    AssetInfo,
    DiagnosticInfo,
    DimensionList,
    DimensionMappingResult,
    DimensionView,
    MaterialInfo,
    Observation,
    Participant,
    ParticipantList,
    PatternList,
    ProblemDimensionItem,
    ProblemPattern,
    ProblemStudentItem,
    ProblemStudentList,
    SourceLocator,
    StudentCodeEvidence,
    SubmissionEvidence,
    TestPointFailure,
    VerdictCount,
)
from app.edu.auth import Principal, require_scope, require_teacher
from app.edu.originals import OriginalStore
from app.importing.edu.answersheet import (
    STATUS_FAIL,
    STATUS_NOT_ATTEMPTED,
    STATUS_PARTIAL,
    STATUS_PASS,
    STATUS_UNKNOWN,
    AnswerSheetEvidence,
    classify_status,
)
from app.storage.database import Database

ANSWERSHEET_PARSER_VERSION = "answersheet.v1"

# 五维固定编码；映射配置只能取这些值。
DIMENSION_CODES: tuple[str, ...] = (
    "algorithm_data_structure",
    "code_implementation",
    "problem_modeling",
    "efficiency_awareness",
    "solution_strategy",
)

# 分数与内部表示统一按百分之一分存，和现有 evaluations 约定一致。
_SCORE_SCALE = 100

# 出现这些诊断时，人数与统计只能标为"待确认"，不得当作正式结论发布。
_REVIEW_DIAGNOSTIC_CODES = frozenset(
    {
        "PARTICIPANT_SERIES_MIXED",
        "PROBLEM_COUNT_MISMATCH",
        "PROBLEM_TOTAL_SCORE_CONFLICT",
    }
)

# "失败测试点"与统计口径一致：只有明确失败才算，未知与未作答都不算。
# 历史行没有 status_class，用原文里的已知失败词兜底，判不出来的一律不算失败。
_FAILED_OBSERVATION = (
    "(t.status_class = 'FAIL' OR (t.status_class IS NULL AND t.status_known = 1 "
    "AND t.status IN ('答案错误','运行时错误','运行超时','运行错误','内存超限',"
    "'段错误','非零返回','编译错误','格式错误')))"
)


@dataclass(frozen=True)
class AssetInput:
    """一个上传包的资产信息；``entries`` 是它包内的材料路径，用于把证据绑回资产。

    ``payload`` 是这个包的原始字节，交给受控原件仓库按内容哈希保存。
    """

    entry: str
    kind: str
    sha256: str
    byte_size: int
    source_index: int
    entries: frozenset[str] = frozenset()
    payload: bytes = b""


@dataclass(frozen=True)
class OriginalEvidence:
    """服务端回读到的原件：字节 + 已校验的摘要 + 它所属的来源快照。"""

    assessment_id: str
    asset_id: str
    entry: str
    kind: str
    sha256: str
    byte_size: int
    source_index: int
    source_snapshot_id: str | None
    payload: bytes


def _source_snapshot_id(assessment_id: str, assets: Sequence[AssetInput]) -> str:
    """来源快照：绑定测评与资产集合（含每个包的哈希），与写入顺序无关。"""
    payload = {
        "assessment_id": assessment_id,
        "assets": sorted(
            [
                {
                    "entry": asset.entry,
                    "kind": asset.kind,
                    "sha256": asset.sha256,
                    "byte_size": asset.byte_size,
                    "source_index": asset.source_index,
                }
                for asset in assets
            ],
            key=lambda item: (item["source_index"], item["entry"]),
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AssessmentSummary:
    assessment_id: str
    student_count: int
    problem_count: int
    submission_count: int
    observation_count: int
    duplicate: bool


def _score_total_conflicts(evidence: AnswerSheetEvidence) -> set[str]:
    """同一道题在不同文件里满分不一致：这类题不得生成可信得分率。"""
    totals: dict[str, set[int]] = {}
    for submission in evidence.submissions:
        if submission.score_total is not None:
            totals.setdefault(submission.problem_label, set()).add(
                submission.score_total * _SCORE_SCALE
            )
    return {label for label, values in totals.items() if len(values) > 1}


class _AssetBinder:
    """把解析出的记录绑回上传包，不靠同名路径猜来源。

    首选"包号 + 包内路径"：包号来自上传顺序，路径还必须命中这个包自己的成员清单，
    因此两个 ZIP 含同一内部路径时各自绑自己的包，不会串证据。
    记录没有包号（直接调用解析器的旧调用方）时才退回"全批次路径唯一"的旧判定；
    仍无法唯一确定就保持未绑定并登记诊断，绝不静默绑错包。
    """

    def __init__(self) -> None:
        self.sha256: dict[str, str] = {}
        self._by_entry: dict[str, list[str]] = {}
        self._by_package: dict[int, tuple[str, frozenset[str]]] = {}
        self.ambiguous: set[str] = set()
        self.unresolved: set[str] = set()

    def add(self, asset: AssetInput, asset_id: str) -> None:
        self.sha256[asset_id] = asset.sha256
        self._by_package[asset.source_index] = (asset_id, frozenset(asset.entries))
        for entry in asset.entries:
            self._by_entry.setdefault(entry, []).append(asset_id)

    def for_package_entry(self, package_index: int | None, entry: str | None) -> str | None:
        if entry is None:
            return None
        if package_index is not None:
            package = self._by_package.get(package_index)
            if package is not None and entry in package[1]:
                return package[0]
            # 包号已知但路径不在该包：宁可判为来源不明，也不拿同名路径顶替
            self.unresolved.add(entry)
            return None
        return self.for_entry(entry)

    def for_entry(self, entry: str | None) -> str | None:
        if entry is None:
            return None
        candidates = self._by_entry.get(entry) or []
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            self.ambiguous.add(entry)
        return None


class AssessmentService:
    def __init__(
        self,
        database: Database,
        clock: Callable[[], datetime] | None = None,
        *,
        originals: OriginalStore | None = None,
    ) -> None:
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))
        self.originals = originals

    def create_answersheet(
        self,
        principal: Principal,
        data: bytes,
        *,
        data_scope_id: str,
        title: str,
        course: str | None,
        assessment_date: str | None,
        evidence: AnswerSheetEvidence,
        file_sha256: str | None = None,
        assets: Sequence[AssetInput] = (),
    ) -> AssessmentSummary:
        # 多文件上传时由调用方传入"合并后的摘要"，单文件时可用原始字节计算。
        digest = file_sha256 or hashlib.sha256(data).hexdigest()
        now = self.clock()
        stamp = _stamp(now)
        conflicts = _score_total_conflicts(evidence)

        # 原件先落受控存储，再写数据库；任何一步失败都回滚本次新建的原件，
        # 不发布"有结构化结果却没有有效原件"的测评。
        created: list[str] = []
        storage_state = "absent"
        try:
            if self.originals is not None and assets:
                for asset in assets:
                    if self.originals.put(asset.sha256, asset.payload):
                        created.append(asset.sha256)
                storage_state = "stored"
            with self.database.transaction(write=True) as connection:
                require_teacher(connection, principal, _stamp(now))
                require_scope(connection, principal, data_scope_id, importing=True)

                existing = connection.execute(
                    "SELECT * FROM assessments WHERE data_scope_id=? AND file_sha256=?",
                    (data_scope_id, digest),
                ).fetchone()
                if existing is not None:
                    # 同一份文件重复上传：返回原测评，不重复入库，也不留下无引用的原件。
                    if self.originals is not None:
                        for sha256 in created:
                            self.originals.discard(sha256)
                    return _summary(existing, duplicate=True)

                assessment_id = str(uuid4())
                binder = _AssetBinder()
                student_pks = _resolve_students(connection, data_scope_id, evidence, now)

                reasons = {
                    diagnostic.code
                    for diagnostic in evidence.diagnostics
                    if diagnostic.code in _REVIEW_DIAGNOSTIC_CODES
                }
                if any(material.needs_confirmation for material in evidence.materials):
                    reasons.add("MATERIAL_ROLE_UNCONFIRMED")
                if conflicts:
                    reasons.add("PROBLEM_TOTAL_SCORE_CONFLICT")
                review_state = "needs_review" if reasons else "confirmed"

                connection.execute(
                    "INSERT INTO assessments (assessment_id,data_scope_id,owner_teacher_id,title,"
                    "course,assessment_date,file_sha256,parser_version,student_count,problem_count,"
                    "submission_count,observation_count,created_at,order_basis,review_state,"
                    "review_reasons,source_snapshot_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        assessment_id,
                        data_scope_id,
                        principal.teacher_id,
                        title,
                        course,
                        assessment_date,
                        digest,
                        ANSWERSHEET_PARSER_VERSION,
                        len(evidence.students),
                        len(evidence.problems),
                        len(evidence.submissions),
                        len(evidence.observations),
                        stamp,
                        evidence.order_basis,
                        review_state,
                        json.dumps(sorted(reasons), ensure_ascii=False),
                        _source_snapshot_id(assessment_id, assets),
                    ),
                )

                for asset in assets:
                    asset_id = str(uuid4())
                    binder.add(asset, asset_id)
                    connection.execute(
                        "INSERT INTO assessment_assets (asset_id,assessment_id,entry,kind,"
                        "sha256,byte_size,source_index,created_at,storage_state) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            asset_id,
                            assessment_id,
                            asset.entry,
                            asset.kind,
                            asset.sha256,
                            asset.byte_size,
                            asset.source_index,
                            stamp,
                            storage_state,
                        ),
                    )
                material_keys: set[tuple[str, str]] = set()
                for material in evidence.materials:
                    # 材料身份 = 来源包 + 包内路径。多个包含同一内部路径时，
                    # 各自绑自己的包，不再共用一条批次级记录。
                    material_asset = binder.for_package_entry(
                        material.package_index, material.entry
                    )
                    if material_asset is None and len(assets) > 1:
                        raise APIError(
                            409,
                            "SOURCE_AMBIGUOUS",
                            f"材料 {material.entry} 无法确定来自哪个上传包，拒绝静默绑定",
                        )
                    key = (material_asset or "", material.entry)
                    if key in material_keys:
                        raise APIError(
                            409,
                            "SOURCE_BINDING_CONFLICT",
                            f"同一来源包内重复登记材料 {material.entry}",
                        )
                    material_keys.add(key)
                    connection.execute(
                        "INSERT INTO assessment_materials (material_id,assessment_id,entry,"
                        "category,"
                        "basis,signals,needs_confirmation,asset_id) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            str(uuid4()),
                            assessment_id,
                            material.entry,
                            material.category,
                            material.basis,
                            json.dumps(list(material.signals), ensure_ascii=False),
                            int(material.needs_confirmation),
                            material_asset,
                        ),
                    )
                for diagnostic in evidence.diagnostics:
                    connection.execute(
                        "INSERT INTO assessment_diagnostics VALUES (?,?,?,?,?,?)",
                        (
                            str(uuid4()),
                            assessment_id,
                            diagnostic.code,
                            diagnostic.entry,
                            diagnostic.detail,
                            stamp,
                        ),
                    )
                for entry, reason in evidence.skipped:
                    connection.execute(
                        "INSERT INTO assessment_diagnostics VALUES (?,?,?,?,?,?)",
                        (str(uuid4()), assessment_id, "ENTRY_SKIPPED", entry, reason, stamp),
                    )

                for record in evidence.students:
                    connection.execute(
                        "INSERT INTO assessment_participants (assessment_id,student_pk,"
                        "external_key,"
                        "display_name,total_score_units,score_units,started_at,finished_at,"
                        "duration_minutes,source_asset_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            assessment_id,
                            student_pks[record.participant_key],
                            record.participant_key,
                            record.display_name,
                            None
                            if record.total_score is None
                            else record.total_score * _SCORE_SCALE,
                            None if record.score is None else record.score * _SCORE_SCALE,
                            record.started_at,
                            record.finished_at,
                            record.duration_minutes,
                            binder.for_package_entry(record.package_index, record.source_entry),
                        ),
                    )

                problem_ids = _insert_problems(connection, assessment_id, evidence, conflicts)
                submission_ids = _insert_submissions(
                    connection, assessment_id, evidence, student_pks, problem_ids, now, binder
                )
                _insert_observations(connection, evidence, submission_ids)
                for entry in sorted(binder.ambiguous):
                    connection.execute(
                        "INSERT INTO assessment_diagnostics VALUES (?,?,?,?,?,?)",
                        (
                            str(uuid4()),
                            assessment_id,
                            "SOURCE_ENTRY_AMBIGUOUS",
                            entry,
                            "该路径出现在多个上传包中，无法唯一绑定资产，来源标记为未绑定",
                            stamp,
                        ),
                    )
                for entry in sorted(binder.unresolved):
                    connection.execute(
                        "INSERT INTO assessment_diagnostics VALUES (?,?,?,?,?,?)",
                        (
                            str(uuid4()),
                            assessment_id,
                            "SOURCE_ENTRY_UNRESOLVED",
                            entry,
                            "该路径不在其所属上传包的成员清单里，来源标记为未绑定",
                            stamp,
                        ),
                    )

                row = connection.execute(
                    "SELECT * FROM assessments WHERE assessment_id=?", (assessment_id,)
                ).fetchone()

        except BaseException:
            if self.originals is not None:
                for sha256 in created:
                    self.originals.discard(sha256)
            raise

        return _summary(row, duplicate=False)

    def set_problem_dimensions(
        self, principal: Principal, assessment_id: str, items: list[tuple[str, str]]
    ) -> DimensionMappingResult:
        """配置"题目 → 维度"映射；整体替换，不做增量合并。"""
        labels = [label for label, _ in items]
        if len(set(labels)) != len(labels):
            raise APIError(422, "VALIDATION_ERROR", "同一道题不能映射到多个维度")

        with self.database.transaction(write=True) as connection:
            _owned_assessment(connection, principal, assessment_id)
            problems = {
                row["label"]: row["problem_id"]
                for row in connection.execute(
                    "SELECT problem_id, label FROM problems WHERE assessment_id=?",
                    (assessment_id,),
                )
            }
            cleaned: list[tuple[str, str, str]] = []
            for label, code in items:
                if code not in DIMENSION_CODES:
                    raise APIError(422, "VALIDATION_ERROR", f"未知维度编码：{code}")
                problem_id = problems.get(label)
                if problem_id is None:
                    raise APIError(422, "VALIDATION_ERROR", f"该测评没有题目：{label}")
                cleaned.append((problem_id, label, code))

            connection.execute(
                "DELETE FROM problem_dimensions WHERE assessment_id=?", (assessment_id,)
            )
            now = _stamp(self.clock())
            for problem_id, _label, code in cleaned:
                connection.execute(
                    "INSERT INTO problem_dimensions VALUES (?,?,?,?)",
                    (problem_id, assessment_id, code, now),
                )

        return DimensionMappingResult(
            assessment_id=assessment_id,
            mapped_count=len(cleaned),
            items=[
                ProblemDimensionItem(problem_label=label, dimension_code=code)
                for _problem_id, label, code in cleaned
            ],
        )


def _owned_assessment(
    connection: sqlite3.Connection, principal: Principal, assessment_id: str
) -> sqlite3.Row:
    """不存在与无权访问统一返回 404，避免探测他人的测评 ID。"""
    row = connection.execute(
        "SELECT * FROM assessments WHERE assessment_id=?", (assessment_id,)
    ).fetchone()
    if row is None or row["owner_teacher_id"] != principal.teacher_id:
        raise APIError(404, "RESOURCE_NOT_FOUND", "测评不存在")
    return row


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


class AssessmentReader:
    """读取答题卡测评与个人证据。目前只允许测评拥有者访问。"""

    def __init__(self, database: Database, *, originals: OriginalStore | None = None) -> None:
        self.database = database
        self.originals = originals

    def original(
        self,
        principal: Principal,
        assessment_id: str,
        asset_id: str,
        *,
        expected_sha256: str | None = None,
    ) -> OriginalEvidence:
        """服务端回读原件：先验归属与资产引用，再校验实际文件哈希。

        没有公开下载接口；原件缺失、被改动或来源撤销都明确拒绝。
        """
        with self.database.transaction() as connection:
            row = self._owned(connection, principal, assessment_id)
            asset = connection.execute(
                "SELECT * FROM assessment_assets WHERE assessment_id=? AND asset_id=?",
                (assessment_id, asset_id),
            ).fetchone()
            if asset is None:
                raise APIError(404, "RESOURCE_NOT_FOUND", "该测评中没有此资产")
            if asset["storage_state"] != "stored":
                raise APIError(
                    409, "ORIGINAL_UNAVAILABLE", "该资产没有可回读的受控原件或来源已撤销"
                )
            if expected_sha256 is not None and expected_sha256 != asset["sha256"]:
                raise APIError(409, "SOURCE_CHANGED", "调用方持有的摘要与该资产不一致")
            if self.originals is None:
                raise APIError(409, "ORIGINAL_UNAVAILABLE", "服务未配置原件存储，无法回读原件")
            payload = self.originals.read(asset["sha256"])
        return OriginalEvidence(
            assessment_id=assessment_id,
            asset_id=asset["asset_id"],
            entry=asset["entry"],
            kind=asset["kind"],
            sha256=asset["sha256"],
            byte_size=len(payload),
            source_index=asset["source_index"],
            source_snapshot_id=row["source_snapshot_id"],
            payload=payload,
        )

    def overview(self, principal: Principal, assessment_id: str) -> AssessmentOverview:
        with self.database.transaction() as connection:
            row = self._owned(connection, principal, assessment_id)
            assets = connection.execute(
                "SELECT * FROM assessment_assets WHERE assessment_id=? ORDER BY source_index",
                (assessment_id,),
            ).fetchall()
            materials = connection.execute(
                "SELECT * FROM assessment_materials WHERE assessment_id=? ORDER BY entry",
                (assessment_id,),
            ).fetchall()
            diagnostics = connection.execute(
                "SELECT * FROM assessment_diagnostics WHERE assessment_id=? ORDER BY code, entry",
                (assessment_id,),
            ).fetchall()
        reasons = json.loads(row["review_reasons"] or "[]")
        return AssessmentOverview(
            assessment_id=row["assessment_id"],
            title=row["title"],
            course=row["course"],
            assessment_date=row["assessment_date"],
            student_count=row["student_count"],
            problem_count=row["problem_count"],
            submission_count=row["submission_count"],
            observation_count=row["observation_count"],
            created_at=row["created_at"],
            order_basis=row["order_basis"],
            review_state=row["review_state"],
            review_reasons=reasons,
            counts_provisional=row["review_state"] == "needs_review",
            source_snapshot_id=row["source_snapshot_id"],
            assets=[
                AssetInfo(
                    asset_id=asset["asset_id"],
                    entry=asset["entry"],
                    kind=asset["kind"],
                    sha256=asset["sha256"],
                    byte_size=asset["byte_size"],
                    source_index=asset["source_index"],
                    storage_state=asset["storage_state"],
                )
                for asset in assets
            ],
            materials=[
                MaterialInfo(
                    entry=material["entry"],
                    category=material["category"],
                    basis=material["basis"],
                    needs_confirmation=bool(material["needs_confirmation"]),
                    asset_id=material["asset_id"],
                )
                for material in materials
            ],
            diagnostics=[
                DiagnosticInfo(
                    code=diagnostic["code"],
                    entry=diagnostic["entry"],
                    message=diagnostic["message"],
                )
                for diagnostic in diagnostics
            ],
        )

    def participants(self, principal: Principal, assessment_id: str) -> ParticipantList:
        with self.database.transaction() as connection:
            self._owned(connection, principal, assessment_id)
            rows = connection.execute(
                "SELECT student_pk, external_key, display_name, total_score_units, score_units, "
                "started_at, finished_at, duration_minutes FROM assessment_participants "
                "WHERE assessment_id=? ORDER BY external_key",
                (assessment_id,),
            ).fetchall()
        return ParticipantList(
            assessment_id=assessment_id,
            total_count=len(rows),
            items=[
                Participant(
                    student_pk=row["student_pk"],
                    external_key=row["external_key"],
                    display_name=row["display_name"],
                    total_score=_score(row["total_score_units"]),
                    score=_score(row["score_units"]),
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                    duration_minutes=row["duration_minutes"],
                )
                for row in rows
            ],
        )

    def student(
        self,
        principal: Principal,
        assessment_id: str,
        student_pk: str,
        *,
        asset_sha256: str | None = None,
        source_snapshot_id: str | None = None,
    ) -> StudentCodeEvidence:
        with self.database.transaction() as connection:
            assessment = self._owned(connection, principal, assessment_id)
            if (
                source_snapshot_id is not None
                and source_snapshot_id != assessment["source_snapshot_id"]
            ):
                raise APIError(409, "SOURCE_CHANGED", "来源快照与当前测评不一致，请重新获取快照")
            participant = connection.execute(
                "SELECT * FROM assessment_participants WHERE assessment_id=? AND student_pk=?",
                (assessment_id, student_pk),
            ).fetchone()
            if participant is None:
                raise APIError(404, "RESOURCE_NOT_FOUND", "该测评中没有此学生")
            rows = connection.execute(
                "SELECT s.*, p.label, p.ordinal, a.sha256 AS asset_sha256 "
                "FROM submissions s JOIN problems p ON p.problem_id=s.problem_id "
                "LEFT JOIN assessment_assets a ON a.asset_id=s.source_asset_id "
                "WHERE s.assessment_id=? AND s.student_pk=? ORDER BY p.ordinal",
                (assessment_id, student_pk),
            ).fetchall()
            if asset_sha256 is not None:
                self._require_source_match(connection, assessment_id, rows, asset_sha256)
            submissions = [
                SubmissionEvidence(
                    problem_label=row["label"],
                    ordinal=row["ordinal"],
                    language=row["language"],
                    code=row["code_text"],
                    verdict=row["verdict"],
                    score=_score(row["score_units"]),
                    score_total=_score(row["score_total_units"]),
                    code_state=row["code_state"],
                    code_truncated=bool(row["code_truncated"]),
                    compiler_output=row["compiler_output"],
                    compiler_output_truncated=bool(row["compiler_output_truncated"]),
                    verdict_class=row["verdict_class"]
                    or classify_status(row["verdict"]),
                    source=SourceLocator(
                        asset_id=row["source_asset_id"],
                        asset_sha256=row["asset_sha256"],
                        entry=row["source_entry"],
                        source_index=row["source_index"],
                    ),
                    observations=[
                        Observation(
                            test_index=item["test_index"],
                            status=item["status"],
                            score=_score(item["score_units"]),
                            duration_ms=item["duration_ms"],
                            memory_kb=item["memory_kb"],
                            status_known=bool(item["status_known"]),
                            status_class=item["status_class"]
                            or classify_status(
                                item["status"], known=bool(item["status_known"])
                            ),
                            duration_us=item["duration_us"],
                            duration_text=item["duration_text"],
                            memory_text=item["memory_text"],
                            source_row=item["source_row"],
                        )
                        for item in connection.execute(
                            "SELECT * FROM test_observations WHERE submission_id=? "
                            "ORDER BY test_index",
                            (row["submission_id"],),
                        ).fetchall()
                    ],
                )
                for row in rows
            ]
        return StudentCodeEvidence(
            assessment_id=assessment_id,
            student_pk=participant["student_pk"],
            external_key=participant["external_key"],
            display_name=participant["display_name"],
            source_snapshot_id=assessment["source_snapshot_id"],
            submissions=submissions,
        )

    @staticmethod
    def _require_source_match(
        connection: sqlite3.Connection,
        assessment_id: str,
        rows: Sequence[sqlite3.Row],
        asset_sha256: str,
    ) -> None:
        """摘要要证明的是"这个学生这些证据"的来源包，不是"属于同一批上传"。

        A 包的摘要不能用来证明 B 包里的代码或测试点；证据跨多个包时，
        单一摘要无法证明全部来源，同样拒绝。
        """
        bound = {row["asset_sha256"] for row in rows if row["asset_sha256"] is not None}
        unbound = any(row["asset_sha256"] is None for row in rows)
        if not bound:
            # 历史批次没有资产绑定：退回批次级校验，保持旧数据可读。
            known = connection.execute(
                "SELECT 1 FROM assessment_assets WHERE assessment_id=? AND sha256=?",
                (assessment_id, asset_sha256),
            ).fetchone()
            if known is None:
                raise APIError(409, "SOURCE_CHANGED", "来源摘要与本批资料不一致，请重新上传")
            return
        if unbound or len(bound) != 1 or asset_sha256 not in bound:
            raise APIError(
                409, "SOURCE_CHANGED", "来源摘要与该学生证据的实际来源包不一致，拒绝回读"
            )

    def problem_patterns(self, principal: Principal, assessment_id: str) -> PatternList:
        """按题目聚合确定性错误现象；不做任何推测性解释。"""
        with self.database.transaction() as connection:
            self._owned(connection, principal, assessment_id)
            problems = connection.execute(
                "SELECT problem_id, label, ordinal, total_score_conflict FROM problems "
                "WHERE assessment_id=? ORDER BY ordinal",
                (assessment_id,),
            ).fetchall()
            items = [self._pattern(connection, row) for row in problems]
        return PatternList(assessment_id=assessment_id, items=items)

    def dimensions(self, principal: Principal, assessment_id: str) -> DimensionList:
        """五维视图：没映射时只报"无评分数据"，映射后才按题目得分推算。"""
        with self.database.transaction() as connection:
            assessment = self._owned(connection, principal, assessment_id)
            rows = connection.execute(
                "SELECT p.label, p.problem_id, d.dimension_code FROM problems p "
                "LEFT JOIN problem_dimensions d ON d.problem_id = p.problem_id "
                "WHERE p.assessment_id=? ORDER BY p.ordinal",
                (assessment_id,),
            ).fetchall()

        mapped: dict[str, list[tuple[str, str]]] = {code: [] for code in DIMENSION_CODES}
        for row in rows:
            code = row["dimension_code"]
            if code in mapped:
                mapped[code].append((row["label"], row["problem_id"]))

        items: list[DimensionView] = []
        for code in DIMENSION_CODES:
            pairs = mapped[code]
            if not pairs:
                items.append(
                    DimensionView(
                        dimension_code=code,
                        state="no_scoring_data",
                        problem_labels=[],
                        submission_count=0,
                        full_pass_count=0,
                        unanswered_count=0,
                        score_rate=None,
                        test_pass_rate=None,
                        note="本批没有该维度的评分数据，也没有配置题目映射",
                    )
                )
                continue
            items.append(self._dimension_from_problems(principal, assessment_id, code, pairs))
        return DimensionList(
            assessment_id=assessment_id,
            mapping_configured=any(mapped[code] for code in DIMENSION_CODES),
            items=items,
            review_state=assessment["review_state"],
        )

    def _dimension_from_problems(
        self,
        principal: Principal,
        assessment_id: str,
        code: str,
        pairs: list[tuple[str, str]],
    ) -> DimensionView:
        labels = [label for label, _ in pairs]
        ids = [problem_id for _, problem_id in pairs]
        placeholders = ",".join("?" for _ in ids)
        with self.database.transaction() as connection:
            self._owned(connection, principal, assessment_id)
            submissions = connection.execute(
                "SELECT score_units, score_total_units, verdict, verdict_class FROM submissions "
                f"WHERE problem_id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
            observations = connection.execute(
                "SELECT t.status, t.status_known, t.status_class FROM test_observations t "
                "JOIN submissions s ON s.submission_id = t.submission_id "
                f"WHERE s.problem_id IN ({placeholders})",
                tuple(ids),
            ).fetchall()

        earned = 0
        possible = 0
        full_pass = 0
        unanswered = 0
        for row in submissions:
            units, total = row["score_units"], row["score_total_units"]
            if (row["verdict_class"] or classify_status(row["verdict"])) == STATUS_NOT_ATTEMPTED:
                unanswered += 1
            if units is None or total is None:
                continue
            earned += units
            possible += total
            if units == total and total > 0:
                full_pass += 1
        passed = 0
        effective = 0
        unknown = 0
        for row in observations:
            status_class = row["status_class"] or classify_status(
                row["status"], known=bool(row["status_known"])
            )
            if status_class == STATUS_PASS:
                passed += 1
            if status_class in (STATUS_PASS, STATUS_FAIL, STATUS_PARTIAL):
                effective += 1
            if status_class == STATUS_UNKNOWN:
                unknown += 1
        note = "由已配置题目的得分推算，不是官方五维评分"
        if unknown:
            note += f"；{unknown} 条测试点状态未知，未计入通过率"

        return DimensionView(
            dimension_code=code,
            state="derived_from_problem_scores",
            problem_labels=labels,
            submission_count=len(submissions),
            full_pass_count=full_pass,
            unanswered_count=unanswered,
            score_rate=round(earned / possible, 4) if possible else None,
            test_pass_rate=round(passed / effective, 4) if effective else None,
            note=note,
        )

    def problem_students(
        self,
        principal: Principal,
        assessment_id: str,
        problem_label: str,
        *,
        status: str | None = None,
        test_index: int | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> ProblemStudentList:
        """从"某题的错误现象"钻到具体学生：可按结论或某个失败测试点过滤。"""
        if offset < 0 or not 1 <= limit <= 100:
            raise APIError(422, "VALIDATION_ERROR", "分页参数不合法")

        filters = ["s.assessment_id = ?", "pr.label = ?"]
        params: list[object] = [assessment_id, problem_label]
        if status is not None:
            filters.append("s.verdict = ?")
            params.append(status)
        if test_index is not None:
            filters.append(
                "EXISTS (SELECT 1 FROM test_observations t "
                "WHERE t.submission_id = s.submission_id AND t.test_index = ? "
                f"AND {_FAILED_OBSERVATION})"
            )
            params.append(test_index)
        base = (
            "FROM submissions s JOIN problems pr ON pr.problem_id = s.problem_id "
            "JOIN assessment_participants p ON p.assessment_id = s.assessment_id "
            "AND p.student_pk = s.student_pk WHERE " + " AND ".join(filters)
        )

        with self.database.transaction() as connection:
            self._owned(connection, principal, assessment_id)
            known = connection.execute(
                "SELECT 1 FROM problems WHERE assessment_id=? AND label=?",
                (assessment_id, problem_label),
            ).fetchone()
            if known is None:
                raise APIError(404, "RESOURCE_NOT_FOUND", "该测评中没有此题目")
            total = connection.execute("SELECT COUNT(*) " + base, tuple(params)).fetchone()[0]
            rows = connection.execute(
                "SELECT s.submission_id, s.student_pk, p.external_key, p.display_name, "
                "s.verdict, s.score_units, s.score_total_units "
                + base
                + " ORDER BY p.external_key LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            failures: dict[str, list[int]] = {}
            ids = [row["submission_id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                for item in connection.execute(
                    "SELECT submission_id, test_index FROM test_observations t "
                    f"WHERE submission_id IN ({placeholders}) AND {_FAILED_OBSERVATION} "
                    "ORDER BY test_index",
                    tuple(ids),
                ):
                    failures.setdefault(item["submission_id"], []).append(item["test_index"])

        return ProblemStudentList(
            assessment_id=assessment_id,
            problem_label=problem_label,
            total_count=total,
            offset=offset,
            limit=limit,
            items=[
                ProblemStudentItem(
                    student_pk=row["student_pk"],
                    external_key=row["external_key"],
                    display_name=row["display_name"],
                    verdict=row["verdict"],
                    score=_score(row["score_units"]),
                    score_total=_score(row["score_total_units"]),
                    failed_test_points=failures.get(row["submission_id"], []),
                )
                for row in rows
            ],
        )

    def _pattern(self, connection: sqlite3.Connection, problem: sqlite3.Row) -> ProblemPattern:
        submissions = connection.execute(
            "SELECT score_units, score_total_units, verdict, verdict_class FROM submissions "
            "WHERE problem_id=?",
            (problem["problem_id"],),
        ).fetchall()
        observations = connection.execute(
            "SELECT t.test_index, t.status, t.status_known, t.status_class "
            "FROM test_observations t "
            "JOIN submissions s ON s.submission_id=t.submission_id WHERE s.problem_id=?",
            (problem["problem_id"],),
        ).fetchall()
        conflict = bool(problem["total_score_conflict"])

        verdict_counts: dict[str, int] = {}
        full_pass = 0
        zero = 0
        total_score = 0.0
        scored = 0
        for row in submissions:
            status = row["verdict"] or "未标注"
            verdict_counts[status] = verdict_counts.get(status, 0) + 1
            units = row["score_units"]
            total_units = row["score_total_units"]
            if units is not None and total_units is not None:
                scored += 1
                total_score += units / _SCORE_SCALE
                if units == total_units and total_units > 0:
                    full_pass += 1
            if units == 0:
                zero += 1

        counts = {
            STATUS_PASS: 0,
            STATUS_FAIL: 0,
            STATUS_PARTIAL: 0,
            STATUS_NOT_ATTEMPTED: 0,
            STATUS_UNKNOWN: 0,
        }
        failures: dict[int, int] = {}
        for row in observations:
            # 只有明确失败才计失败；未知、未作答、部分正确都单列，不进失败率分母。
            status_class = row["status_class"] or classify_status(
                row["status"], known=bool(row["status_known"])
            )
            counts[status_class] += 1
            if status_class == STATUS_FAIL:
                failures[row["test_index"]] = failures.get(row["test_index"], 0) + 1
        failure_count = sum(failures.values())
        worst = sorted(failures.items(), key=lambda item: (-item[1], item[0]))[:3]
        effective = counts[STATUS_PASS] + counts[STATUS_FAIL] + counts[STATUS_PARTIAL]
        observed = len(observations)

        return ProblemPattern(
            problem_label=problem["label"],
            ordinal=problem["ordinal"],
            submission_count=len(submissions),
            full_pass_count=full_pass,
            zero_score_count=zero,
            # 同题满分冲突时不给出均分，避免拿任意一个满分当基准
            average_score=None
            if conflict
            else (round(total_score / scored, 2) if scored else None),
            verdict_counts=[
                VerdictCount(status=status, count=count)
                for status, count in sorted(
                    verdict_counts.items(), key=lambda item: (-item[1], item[0])
                )
            ],
            test_point_count=len({row["test_index"] for row in observations}),
            failure_count=failure_count,
            failure_rate=round(failure_count / effective, 4) if effective else None,
            worst_test_points=[
                TestPointFailure(test_index=index, failure_count=count) for index, count in worst
            ],
            pass_count=counts[STATUS_PASS],
            fail_count=counts[STATUS_FAIL],
            partial_count=counts[STATUS_PARTIAL],
            not_attempted_count=counts[STATUS_NOT_ATTEMPTED],
            unknown_count=counts[STATUS_UNKNOWN],
            observation_count=observed,
            effective_count=effective,
            coverage=round(effective / observed, 4) if observed else None,
            unknown_rate=round(counts[STATUS_UNKNOWN] / observed, 4) if observed else None,
            score_rate_available=not conflict,
        )

    def _owned(
        self, connection: sqlite3.Connection, principal: Principal, assessment_id: str
    ) -> sqlite3.Row:
        return _owned_assessment(connection, principal, assessment_id)


def _score(units: int | None) -> float | None:
    return None if units is None else units / _SCORE_SCALE


def _summary(row: sqlite3.Row, *, duplicate: bool) -> AssessmentSummary:
    return AssessmentSummary(
        assessment_id=row["assessment_id"],
        student_count=row["student_count"],
        problem_count=row["problem_count"],
        submission_count=row["submission_count"],
        observation_count=row["observation_count"],
        duplicate=duplicate,
    )


def _resolve_students(
    connection: sqlite3.Connection,
    data_scope_id: str,
    evidence: AnswerSheetEvidence,
    now: datetime,
) -> dict[str, str]:
    """按「来源范围 + 考号」找已有学生；找不到才新建，绝不按姓名合并。"""
    resolved: dict[str, str] = {}
    for record in evidence.students:
        row = connection.execute(
            "SELECT p.student_pk FROM assessment_participants p "
            "JOIN assessments a ON a.assessment_id=p.assessment_id "
            "WHERE a.data_scope_id=? AND p.external_key=? LIMIT 1",
            (data_scope_id, record.participant_key),
        ).fetchone()
        if row is not None:
            resolved[record.participant_key] = row["student_pk"]
            continue
        student_pk = str(uuid4())
        connection.execute(
            "INSERT INTO students VALUES (?,?,?,?)",
            (student_pk, str(uuid4()), data_scope_id, _stamp(now)),
        )
        resolved[record.participant_key] = student_pk
    return resolved


def _insert_problems(
    connection: sqlite3.Connection,
    assessment_id: str,
    evidence: AnswerSheetEvidence,
    conflicts: set[str],
) -> dict[str, str]:
    problem_ids: dict[str, str] = {}
    for problem in evidence.problems:
        problem_id = str(uuid4())
        problem_ids[problem.label] = problem_id
        conflict = problem.label in conflicts
        connection.execute(
            "INSERT INTO problems (problem_id,assessment_id,label,ordinal,total_score,"
            "total_score_conflict) VALUES (?,?,?,?,?,?)",
            (
                problem_id,
                assessment_id,
                problem.label,
                problem.display_ordinal,
                # 满分有冲突时不保留任何一个值，避免下游拿它算"可信"得分率
                None
                if conflict or problem.total_score is None
                else problem.total_score * _SCORE_SCALE,
                int(conflict),
            ),
        )
    return problem_ids


def _insert_submissions(
    connection: sqlite3.Connection,
    assessment_id: str,
    evidence: AnswerSheetEvidence,
    student_pks: dict[str, str],
    problem_ids: dict[str, str],
    now: datetime,
    binder: _AssetBinder,
) -> dict[tuple[str, str], str]:
    ids: dict[tuple[str, str], str] = {}
    for submission in evidence.submissions:
        try:
            student_pk = student_pks[submission.participant_key]
            problem_id = problem_ids[submission.problem_label]
        except KeyError:
            # 解析结果与题目/学生清单不一致：整批回滚，不写半套。
            raise APIError(422, "ANSWERSHEET_INCONSISTENT", "答题卡内部引用不一致") from None
        submission_id = str(uuid4())
        ids[(submission.participant_key, submission.problem_label)] = submission_id
        connection.execute(
            "INSERT INTO submissions (submission_id,assessment_id,student_pk,problem_id,language,"
            "code_text,verdict,score_units,score_total_units,created_at,code_state,"
            "code_truncated,compiler_output,compiler_output_truncated,verdict_class,"
            "source_asset_id,source_entry,source_index) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                submission_id,
                assessment_id,
                student_pk,
                problem_id,
                submission.language,
                submission.code or "",
                submission.verdict,
                None if submission.score is None else submission.score * _SCORE_SCALE,
                None if submission.score_total is None else submission.score_total * _SCORE_SCALE,
                _stamp(now),
                submission.code_state,
                int(submission.code_truncated),
                submission.compiler_output,
                int(submission.compiler_output_truncated),
                classify_status(submission.verdict),
                binder.for_package_entry(submission.package_index, submission.source_entry),
                submission.source_entry,
                submission.source_index,
            ),
        )
    return ids


def _insert_observations(
    connection: sqlite3.Connection,
    evidence: AnswerSheetEvidence,
    submission_ids: dict[tuple[str, str], str],
) -> None:
    for observation in evidence.observations:
        submission_id = submission_ids.get((observation.participant_key, observation.problem_label))
        if submission_id is None:
            # 观测指向不存在的提交：与提交侧同口径，整批回滚，不静默丢弃。
            raise APIError(422, "ANSWERSHEET_INCONSISTENT", "测试点找不到对应的提交记录")
        connection.execute(
            "INSERT INTO test_observations (observation_id,submission_id,test_index,status,"
            "score_units,duration_ms,memory_kb,status_known,status_class,duration_us,"
            "duration_text,memory_text,source_row) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                submission_id,
                observation.test_index,
                observation.status,
                None if observation.score is None else observation.score * _SCORE_SCALE,
                # 展示值四舍五入（0.5 → 1）；精确值在 duration_us，原文在 duration_text
                None if observation.duration_ms is None else int(observation.duration_ms + 0.5),
                observation.memory_kb,
                int(observation.status_known),
                classify_status(observation.status, known=observation.status_known),
                # 小数毫秒不能丢：0.5 ms 入库为 500 微秒，而不是被取整成 0 或 1
                None if observation.duration_ms is None else round(observation.duration_ms * 1000),
                observation.duration_text,
                observation.memory_text,
                observation.source_row,
            ),
        )
