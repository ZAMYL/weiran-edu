"""答题卡测评的对外响应模型。"""

from app.edu.models import Model


class AssessmentResult(Model):
    """一次答题卡导入的结果摘要；duplicate 表示命中同一文件的重复上传。"""

    assessment_id: str
    student_count: int
    problem_count: int
    submission_count: int
    observation_count: int
    duplicate: bool


class AssessmentOverview(Model):
    assessment_id: str
    title: str
    course: str | None
    assessment_date: str | None
    student_count: int
    problem_count: int
    submission_count: int
    observation_count: int
    created_at: str


class Participant(Model):
    """考号与姓名取自上传资料，按要求原样展示。"""

    student_pk: str
    external_key: str
    display_name: str


class ParticipantList(Model):
    assessment_id: str
    total_count: int
    items: list[Participant]


class Observation(Model):
    test_index: int
    status: str
    score: float | None
    duration_ms: int | None
    memory_kb: int | None


class SubmissionEvidence(Model):
    problem_label: str
    ordinal: int
    language: str | None
    code: str
    verdict: str | None
    score: float | None
    score_total: float | None
    observations: list[Observation]


class StudentCodeEvidence(Model):
    assessment_id: str
    student_pk: str
    external_key: str
    display_name: str
    submissions: list[SubmissionEvidence]
