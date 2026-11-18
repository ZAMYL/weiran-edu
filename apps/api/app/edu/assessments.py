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
from app.edu.auth import Principal, require_scope, require_teacher
from app.importing.edu.answersheet import AnswerSheetEvidence
from app.storage.database import Database

ANSWERSHEET_PARSER_VERSION = "answersheet.v1"

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
    ) -> AssessmentSummary:
        file_sha256 = hashlib.sha256(data).hexdigest()
        now = self.clock()

        with self.database.transaction(write=True) as connection:
            require_teacher(connection, principal, _stamp(now))
            require_scope(connection, principal, data_scope_id, importing=True)

            existing = connection.execute(
                "SELECT * FROM assessments WHERE data_scope_id=? AND file_sha256=?",
                (data_scope_id, file_sha256),
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
                    file_sha256,
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


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


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
                problem.ordinal,
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
                submission.code,
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
                observation.duration_ms,
                observation.memory_kb,
            ),
        )
