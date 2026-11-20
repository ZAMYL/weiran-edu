"""答题卡测评的对外响应模型。"""

from app.edu.models import Model


class SourceLocator(Model):
    """证据在原始资料里的位置；asset_id 为 None 表示无法唯一绑到某个包。"""

    asset_id: str | None = None
    asset_sha256: str | None = None
    entry: str | None = None
    source_index: int | None = None


class AssetInfo(Model):
    """一个上传包的资产摘要（不含文件内容）。"""

    asset_id: str
    entry: str
    kind: str
    sha256: str
    byte_size: int
    source_index: int
    storage_state: str = "absent"


class MaterialInfo(Model):
    entry: str
    category: str
    basis: str
    needs_confirmation: bool
    asset_id: str | None = None


class DiagnosticInfo(Model):
    code: str
    entry: str | None
    message: str


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
    order_basis: str = "NATURAL_SORT"
    review_state: str = "confirmed"
    review_reasons: list[str] = []
    counts_provisional: bool = False
    source_snapshot_id: str | None = None
    assets: list[AssetInfo] = []
    materials: list[MaterialInfo] = []
    diagnostics: list[DiagnosticInfo] = []


class Participant(Model):
    """考号与姓名取自上传资料，按要求原样展示。"""

    student_pk: str
    external_key: str
    display_name: str
    total_score: float | None = None
    score: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_minutes: int | None = None


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
    status_known: bool = True
    status_class: str = "UNKNOWN"
    duration_us: int | None = None
    duration_text: str = ""
    memory_text: str = ""
    source_row: int | None = None


class SubmissionEvidence(Model):
    problem_label: str
    ordinal: int
    language: str | None
    code: str
    verdict: str | None
    score: float | None
    score_total: float | None
    observations: list[Observation]
    code_state: str = "PRESENT"
    code_truncated: bool = False
    compiler_output: str | None = None
    compiler_output_truncated: bool = False
    verdict_class: str = "UNKNOWN"
    source: SourceLocator | None = None


class StudentCodeEvidence(Model):
    assessment_id: str
    student_pk: str
    external_key: str
    display_name: str
    source_snapshot_id: str | None = None
    submissions: list[SubmissionEvidence]


class VerdictCount(Model):
    status: str
    count: int


class TestPointFailure(Model):
    test_index: int
    failure_count: int


class ProblemPattern(Model):
    """一道题的确定性错误现象：结论分布、满分/零分人数、失败集中在哪些测试点。

    失败率只统计有明确结论的测试点：未知、未作答、被截断的记录单独计数，
    不进入失败率的分母，也不被算作失败。
    """

    problem_label: str
    ordinal: int
    submission_count: int
    full_pass_count: int
    zero_score_count: int
    average_score: float | None
    verdict_counts: list[VerdictCount]
    test_point_count: int
    failure_count: int
    failure_rate: float | None
    worst_test_points: list[TestPointFailure]
    pass_count: int = 0
    fail_count: int = 0
    partial_count: int = 0
    not_attempted_count: int = 0
    unknown_count: int = 0
    observation_count: int = 0
    effective_count: int = 0
    coverage: float | None = None
    unknown_rate: float | None = None
    score_rate_available: bool = True


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


class ProblemDimensionItem(Model):
    """题目 → 维度 的一条配置。"""

    problem_label: str
    dimension_code: str


class ProblemDimensionRequest(Model):
    items: list[ProblemDimensionItem]


class DimensionMappingResult(Model):
    assessment_id: str
    mapped_count: int
    items: list[ProblemDimensionItem]


class DimensionView(Model):
    """五维中的一个维度。

    state：
    - ``no_scoring_data``：本批没有该维度的评分或映射，只展示证据量；
    - ``derived_from_problem_scores``：由已配置的题目得分推算，不是官方五维评分。
    """

    dimension_code: str
    state: str
    problem_labels: list[str]
    submission_count: int
    full_pass_count: int
    unanswered_count: int
    score_rate: float | None
    test_pass_rate: float | None
    note: str


class DimensionList(Model):
    assessment_id: str
    mapping_configured: bool
    items: list[DimensionView]
    review_state: str = "confirmed"
