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


class VerdictCount(Model):
    status: str
    count: int


class TestPointFailure(Model):
    test_index: int
    failure_count: int


class ProblemPattern(Model):
    """一道题的确定性错误现象：结论分布、满分/零分人数、失败集中在哪些测试点。"""

    problem_label: str
    ordinal: int
    submission_count: int
    full_pass_count: int
    zero_score_count: int
    average_score: float | None
    verdict_counts: list[VerdictCount]
    test_point_count: int
    failure_count: int
    failure_rate: float
    worst_test_points: list[TestPointFailure]


class PatternList(Model):
    assessment_id: str
    items: list[ProblemPattern]


class ProblemStudentItem(Model):
    """某道题下一个学生的表现；failed_test_points 只列非"答案正确"的测试点。"""

    student_pk: str
    external_key: str
    display_name: str
    verdict: str | None
    score: float | None
    score_total: float | None
    failed_test_points: list[int]


class ProblemStudentList(Model):
    assessment_id: str
    problem_label: str
    total_count: int
    offset: int
    limit: int
    items: list[ProblemStudentItem]
