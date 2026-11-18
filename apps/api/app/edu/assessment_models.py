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
