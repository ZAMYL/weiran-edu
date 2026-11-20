"""答题卡测评的报告渲染（确定性统计，不含模型推断）。

只做"把已有结论排版成可交付文本"，不重新计算任何数字：
概览与错误模式都来自 AssessmentReader 的读取结果。
"""

from app.edu.assessment_models import AssessmentOverview, PatternList


def render_markdown(overview: AssessmentOverview, patterns: PatternList) -> str:
    lines = [
        "# 未然 EDU · 学情诊断报告",
        "",
        f"- 测评名称：{overview.title}",
    ]
    if overview.course:
        lines.append(f"- 课程：{overview.course}")
    if overview.assessment_date:
        lines.append(f"- 测评日期：{overview.assessment_date}")
    lines += [
        f"- 导入时间：{overview.created_at}",
        f"- 规模：{overview.student_count} 名学生 / {overview.problem_count} 道题 / "
        f"{overview.submission_count} 份提交 / {overview.observation_count} 个测试点",
    ]
    if overview.review_state == "needs_review":
        # 有疑义的批次不能把人数当作已确认结论发布
        lines.append(
            "- **待人工确认**：以下统计为暂定值（原因："
            + "、".join(overview.review_reasons)
            + "）"
        )
    lines += [
        "",
        "## 一、题目错误模式",
        "",
        "| 题号 | 提交 | 满分 | 零分 | 均分 | 测试点失败率 | 未知状态 | 失败最集中的测试点 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in patterns.items:
        worst = (
            "、".join(
                f"T{failure.test_index}（{failure.failure_count} 人）"
                for failure in item.worst_test_points
            )
            or "—"
        )
        average = "—" if item.average_score is None else f"{item.average_score:.2f}"
        rate = "—" if item.failure_rate is None else f"{item.failure_rate:.2%}"
        if not item.score_rate_available:
            average = "满分冲突"
        lines.append(
            f"| {item.problem_label} | {item.submission_count} | {item.full_pass_count} | "
            f"{item.zero_score_count} | {average} | {rate} | {item.unknown_count} | {worst} |"
        )

    lines += [
        "",
        "## 二、结论分布（按题）",
        "",
    ]
    for item in patterns.items:
        counts = "、".join(f"{entry.status} {entry.count}" for entry in item.verdict_counts) or "—"
        lines.append(f"- **{item.problem_label}**：{counts}")

    lines += [
        "",
        "## 三、报告说明",
        "",
        "- 所有人数、比例与得分来自导入资料的确定性统计，未经模型推断。",
        "- 失败率 = 明确失败的测试点数 ÷ 有明确结论的测试点数（通过 + 失败 + 部分正确）；"
        "未知状态、未作答、被截断的记录单列，既不计通过也不计失败。",
        "- 同一题在不同文件里满分不一致时，该题不给出均分，并标记「满分冲突」。",
        "- 本报告不包含学生个人画像结论；个人代码与测试点证据需在平台内按学生查看。",
    ]
    return "\n".join(lines) + "\n"


def render_csv(overview: AssessmentOverview, patterns: PatternList) -> str:
    rows = [
        "problem,submissions,full_pass,zero_score,average_score,failure_rate,"
        "test_points,worst_test_points"
    ]
    for item in patterns.items:
        worst = ";".join(
            f"T{failure.test_index}:{failure.failure_count}" for failure in item.worst_test_points
        )
        average = "" if item.average_score is None else f"{item.average_score:.2f}"
        rate = "" if item.failure_rate is None else f"{item.failure_rate:.4f}"
        rows.append(
            f"{item.problem_label},{item.submission_count},{item.full_pass_count},"
            f"{item.zero_score_count},{average},{rate},"
            f'{item.test_point_count},"{worst}"'
        )
    return "\n".join(rows) + "\n"
