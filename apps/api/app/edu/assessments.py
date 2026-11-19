"""答题卡证据入库：一个事务写入测评、参与者、题目、提交与测试点。

与 Excel 批次（``batches``）并存：那条链路固定五维评分，
这条链路是"代码 + 测试点"证据，两者共用 ``students`` 作为学生身份。
"""

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.core.errors import APIError
from app.edu.assessment_models import (
    AssessmentOverview,
    DimensionList,
    DimensionMappingResult,
    DimensionView,
    Observation,
    Participant,
    ParticipantList,
    PatternList,
    ProblemDimensionItem,
    ProblemPattern,
    ProblemStudentItem,
    ProblemStudentList,
    StudentCodeEvidence,
    SubmissionEvidence,
    TestPointFailure,
    VerdictCount,
)
from app.edu.auth import Principal, require_scope, require_teacher
from app.importing.edu.answersheet import AnswerSheetEvidence
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


@dataclass(frozen=True)
class AssessmentSummary:
    assessment_id: str
    student_count: int
    problem_count: int
    submission_count: int
    observation_count: int
    duplicate: bool


class AssessmentService:
    def __init__(self, database: Database, clock: Callable[[], datetime] | None = None) -> None:
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

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
    ) -> AssessmentSummary:
        # 多文件上传时由调用方传入"合并后的摘要"，单文件时可用原始字节计算。
        digest = file_sha256 or hashlib.sha256(data).hexdigest()
        now = self.clock()

        with self.database.transaction(write=True) as connection:
            require_teacher(connection, principal, _stamp(now))
            require_scope(connection, principal, data_scope_id, importing=True)

            existing = connection.execute(
                "SELECT * FROM assessments WHERE data_scope_id=? AND file_sha256=?",
                (data_scope_id, digest),
            ).fetchone()
            if existing is not None:
                # 同一份文件重复上传：返回原测评，不重复入库。
                return _summary(existing, duplicate=True)

            assessment_id = str(uuid4())
            student_pks = _resolve_students(connection, data_scope_id, evidence, now)
            connection.execute(
                "INSERT INTO assessments (assessment_id,data_scope_id,owner_teacher_id,title,"
                "course,assessment_date,file_sha256,parser_version,student_count,problem_count,"
                "submission_count,observation_count,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    _stamp(now),
                ),
            )
            for record in evidence.students:
                connection.execute(
                    "INSERT INTO assessment_participants VALUES (?,?,?,?)",
                    (
                        assessment_id,
                        student_pks[record.participant_key],
                        record.participant_key,
                        record.display_name,
                    ),
                )

            problem_ids = _insert_problems(connection, assessment_id, evidence)
            submission_ids = _insert_submissions(
                connection, assessment_id, evidence, student_pks, problem_ids, now
            )
            _insert_observations(connection, evidence, submission_ids)

            row = connection.execute(
                "SELECT * FROM assessments WHERE assessment_id=?", (assessment_id,)
            ).fetchone()

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

    def __init__(self, database: Database) -> None:
        self.database = database

    def overview(self, principal: Principal, assessment_id: str) -> AssessmentOverview:
        with self.database.transaction() as connection:
            row = self._owned(connection, principal, assessment_id)
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
        )

    def participants(self, principal: Principal, assessment_id: str) -> ParticipantList:
        with self.database.transaction() as connection:
            self._owned(connection, principal, assessment_id)
            rows = connection.execute(
                "SELECT student_pk, external_key, display_name FROM assessment_participants "
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
                )
                for row in rows
            ],
        )

    def student(
        self, principal: Principal, assessment_id: str, student_pk: str
    ) -> StudentCodeEvidence:
        with self.database.transaction() as connection:
            self._owned(connection, principal, assessment_id)
            participant = connection.execute(
                "SELECT * FROM assessment_participants WHERE assessment_id=? AND student_pk=?",
                (assessment_id, student_pk),
            ).fetchone()
            if participant is None:
                raise APIError(404, "RESOURCE_NOT_FOUND", "该测评中没有此学生")
            rows = connection.execute(
                "SELECT s.submission_id, s.language, s.code_text, s.verdict, "
                "s.score_units, s.score_total_units, p.label, p.ordinal "
                "FROM submissions s JOIN problems p ON p.problem_id=s.problem_id "
                "WHERE s.assessment_id=? AND s.student_pk=? ORDER BY p.ordinal",
                (assessment_id, student_pk),
            ).fetchall()
            submissions = [
                SubmissionEvidence(
                    problem_label=row["label"],
                    ordinal=row["ordinal"],
                    language=row["language"],
                    code=row["code_text"],
                    verdict=row["verdict"],
                    score=_score(row["score_units"]),
                    score_total=_score(row["score_total_units"]),
                    observations=[
                        Observation(
                            test_index=item["test_index"],
                            status=item["status"],
                            score=_score(item["score_units"]),
                            duration_ms=item["duration_ms"],
                            memory_kb=item["memory_kb"],
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
            submissions=submissions,
        )

    def problem_patterns(self, principal: Principal, assessment_id: str) -> PatternList:
        """按题目聚合确定性错误现象；不做任何推测性解释。"""
        with self.database.transaction() as connection:
            self._owned(connection, principal, assessment_id)
            problems = connection.execute(
                "SELECT problem_id, label, ordinal FROM problems "
                "WHERE assessment_id=? ORDER BY ordinal",
                (assessment_id,),
            ).fetchall()
            items = [self._pattern(connection, row) for row in problems]
        return PatternList(assessment_id=assessment_id, items=items)

    def dimensions(self, principal: Principal, assessment_id: str) -> DimensionList:
        """五维视图：没映射时只报"无评分数据"，映射后才按题目得分推算。"""
        with self.database.transaction() as connection:
            self._owned(connection, principal, assessment_id)
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
                "SELECT score_units, score_total_units, verdict FROM submissions "
                f"WHERE problem_id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
            observations = connection.execute(
                "SELECT t.status FROM test_observations t "
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
            if row["verdict"] == "未作答":
                unanswered += 1
            if units is None or total is None:
                continue
            earned += units
            possible += total
            if units == total and total > 0:
                full_pass += 1
        passed = sum(1 for row in observations if row["status"] == "答案正确")

        return DimensionView(
            dimension_code=code,
            state="derived_from_problem_scores",
            problem_labels=labels,
            submission_count=len(submissions),
            full_pass_count=full_pass,
            unanswered_count=unanswered,
            score_rate=round(earned / possible, 4) if possible else None,
            test_pass_rate=round(passed / len(observations), 4) if observations else None,
            note="由已配置题目的得分推算，不是官方五维评分",
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
                "AND t.status <> '答案正确')"
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
                    "SELECT submission_id, test_index FROM test_observations "
                    f"WHERE submission_id IN ({placeholders}) AND status <> '答案正确' "
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
            "SELECT score_units, score_total_units, verdict FROM submissions WHERE problem_id=?",
            (problem["problem_id"],),
        ).fetchall()
        observations = connection.execute(
            "SELECT t.test_index, t.status FROM test_observations t "
            "JOIN submissions s ON s.submission_id=t.submission_id WHERE s.problem_id=?",
            (problem["problem_id"],),
        ).fetchall()

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

        failures: dict[int, int] = {}
        for row in observations:
            if row["status"] == "答案正确":
                continue
            failures[row["test_index"]] = failures.get(row["test_index"], 0) + 1
        failure_count = sum(failures.values())
        worst = sorted(failures.items(), key=lambda item: (-item[1], item[0]))[:3]

        return ProblemPattern(
            problem_label=problem["label"],
            ordinal=problem["ordinal"],
            submission_count=len(submissions),
            full_pass_count=full_pass,
            zero_score_count=zero,
            average_score=round(total_score / scored, 2) if scored else None,
            verdict_counts=[
                VerdictCount(status=status, count=count)
                for status, count in sorted(
                    verdict_counts.items(), key=lambda item: (-item[1], item[0])
                )
            ],
            test_point_count=len({row["test_index"] for row in observations}),
            failure_count=failure_count,
            failure_rate=round(failure_count / len(observations), 4) if observations else 0.0,
            worst_test_points=[
                TestPointFailure(test_index=index, failure_count=count) for index, count in worst
            ],
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
    connection: sqlite3.Connection, assessment_id: str, evidence: AnswerSheetEvidence
) -> dict[str, str]:
    problem_ids: dict[str, str] = {}
    for problem in evidence.problems:
        problem_id = str(uuid4())
        problem_ids[problem.label] = problem_id
        connection.execute(
            "INSERT INTO problems VALUES (?,?,?,?,?)",
            (
                problem_id,
                assessment_id,
                problem.label,
                problem.display_ordinal,
                None if problem.total_score is None else problem.total_score * _SCORE_SCALE,
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
            "INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?)",
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
            continue
        connection.execute(
            "INSERT INTO test_observations VALUES (?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                submission_id,
                observation.test_index,
                observation.status,
                None if observation.score is None else observation.score * _SCORE_SCALE,
                None if observation.duration_ms is None else int(round(observation.duration_ms)),
                observation.memory_kb,
            ),
        )
