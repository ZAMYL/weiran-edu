"""Readable export of the already sanitized quality DTO."""

from .quality import QualityReport

STATUS = {"passed": "通过", "warning": "存在待复核警告", "blocked": "存在阻断错误"}


def render_quality_report(report: QualityReport) -> str:
    """Accept only the public report; never handle raw worksheet cells here."""
    summary = report.summary
    lines = [
        "# Excel 数据质量报告",
        "",
        f"解析结果：**{STATUS[report.status]}**。",
        "",
        "此结果仅判断文件及评分数据质量，入库仍需后续权限与确认事务校验。",
        "",
        f"文件 SHA-256：`{report.file_sha256}`",
        "",
        "| 检查项 | 数量 |",
        "| --- | ---: |",
        f"| 检测到的学生块 | {summary.detected_student_count} |",
        f"| 五维完整且合法的学生 | {summary.complete_student_count} |",
        f"| 评分行 | {summary.parsed_evaluation_count} |",
        f"| 合法数值评分 | {summary.valid_score_count} |",
        f"| 缺失评分 | {summary.missing_score_count} |",
        f"| 非法评分 | {summary.invalid_score_count} |",
        f"| 阻断错误 | {summary.error_count} |",
        f"| 待复核警告 | {summary.warning_count} |",
        f"| 有定位问题的学生 | {summary.affected_student_count} |",
        f"| 工作簿级问题 | {summary.workbook_issue_count} |",
        "",
        "## 五维质量检查",
        "",
        "| 维度 | 评分行 | 合法评分 | 缺分行 | 非法评分 | 缺少该维度的学生 | 维度重复的学生 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for d in report.dimensions:
        lines.append(
            f"| {d.dimension_label} | {d.score_row_count} | {d.valid_score_count} | "
            f"{d.missing_score_count} | {d.invalid_score_count} | "
            f"{d.students_missing_dimension_count} | {d.students_with_duplicate_dimension_count} |"
        )
    lines += [
        "",
        "缺少整个维度与该维度存在但评分为空分别计数；合法数值不代表维度或学生块合法。",
        "",
        "## 问题汇总",
        "",
    ]
    if not report.issue_groups:
        lines.append("未发现阻断错误或待复核警告。")
    else:
        lines += [
            "| 级别 | 问题代码 | 提示 | 次数 | 定位学生数 | 定位行数 |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
        for group in report.issue_groups:
            severity = "错误" if group.severity == "error" else "警告"
            lines.append(
                f"| {severity} | {group.code} | {group.message} | "
                f"{group.occurrence_count} | {group.affected_student_count} | "
                f"{group.affected_row_count} |"
            )
    lines += [
        "",
        "重复评语按受影响记录计数。空评语、重复评语仅提示人工复核，不自动修改原文。",
        "",
        "## 完整问题清单",
        "",
    ]
    if report.issues:
        lines += [
            "| 级别 | 问题代码 | 工作表 | 单元格 | 匿名学生 | 维度编码 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for issue in report.issues:
            severity = "错误" if issue.severity == "error" else "警告"
            lines.append(
                f"| {severity} | {issue.code} | {issue.sheet_name} | {issue.cell} | "
                f"{issue.display_label or '工作簿'} | {issue.dimension_code or '—'} |"
            )
    else:
        lines.append("无。")
    lines += ["", "本报告不包含姓名、学号、原始评语或非法单元格原值。", ""]
    return "\n".join(lines)
