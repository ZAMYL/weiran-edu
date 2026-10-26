"""Synthetic mixed-school/class competition records stay one assessment, never a class."""

import json
from decimal import Decimal

import pytest

from app.importing.edu.types import DIMENSIONS

from .test_http import commit
from .test_report_download import confirm, read_report
from .test_reviewed_results import export_result, save_result

A = "algorithm_data_structure"
S = "solution_strategy"
RULES = [
    {"dimension_code": A, "operator": "lt", "value": 3},
    {"dimension_code": S, "operator": "gte", "value": 4},
]


@pytest.mark.parametrize("format", ["md", "xlsx"])
@pytest.mark.parametrize(
    "metadata", [{}, {"course": "合成显式课程", "assessment_date": "2026-01-02"}]
)
def test_mixed_class_competition_is_scoped_to_assessment(
    client, runtime, book, pack, format, metadata
):
    # Background is private synthetic context, not an API class field or a partition key.
    backgrounds = ("合成甲校甲班", "合成乙校乙班", "合成甲校甲班", "合成乙校乙班")
    algorithms = tuple(map(Decimal, ("2.00", "2.75", "3.00", "4.00")))
    strategies = (4, 5, 4, 3)
    scope_label = "合成丙校丙班-合成赛事-合成课程-2025-03-04"
    scope_id = runtime.admin.create_scope(scope_label)
    runtime.admin.set_member(runtime.teacher, scope_id, can_import=True)
    sheet = book["Sheet2"]
    sheet.delete_rows(1, sheet.max_row)
    sheet.append(["纯合成算法竞赛测评，跨班级参评"])
    for index, background in enumerate(backgrounds):
        sheet.append([f"{index + 1:012d} 合成选手{index}", "维度", "评分（1~5）", "评语"])
        for dimension, spec in enumerate(DIMENSIONS):
            score = (
                algorithms[index] if dimension == 0 else strategies[index] if dimension == 4 else 3
            )
            sheet.append(
                [None, spec.label, score, f"纯合成参评背景 {background}；记录 {index}:{dimension}"]
            )
    response = client.post(
        "/api/v1/edu/imports/preview",
        data={"data_scope_id": scope_id, "title": "合成算法竞赛测评", **metadata},
        files={
            "file": (
                "合成丁校丁班-合成比赛-2024-02-03.xlsx",
                pack(book),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 201, response.text
    preview = response.json()
    assert preview["summary"]["error_count"] == preview["summary"]["warning_count"] == 0
    assert preview["summary"]["complete_student_count"] == 4
    committed = commit(client, preview)
    assert committed.status_code == 201, committed.text
    base = "/api/v1/edu/batches/" + committed.json()["batch_id"]
    batch = client.get(base).json()
    for document in (preview["import"], batch):
        assert document["course"] == metadata.get("course")
        assert document["assessment_date"] == metadata.get("assessment_date")
    assert batch["student_count"] == 4 and batch["evaluation_count"] == 20
    snapshot = {"snapshot_id": batch["snapshot_id"]}

    statistics = client.get(
        base + "/statistics", params={**snapshot, "x_dimension": A, "y_dimension": S}
    ).json()
    assert statistics["scope"] == {"kind": "batch", "record_count": 4}
    algorithm = next(d for d in statistics["dimensions"] if d["dimension_code"] == A)
    # Independently calculated from all four entrants, not either two-person class.
    assert sum(algorithms) / 4 == Decimal("2.9375")
    assert algorithm["mean"]["exact"] == {"numerator": 47, "denominator": 16}
    assert algorithm["valid_count"] == 4
    assert {f["denominator"] for f in algorithm["distribution"]} == {4}
    assert statistics["matrix"]["paired_count"] == 4
    assert statistics["matrix"]["scope"] == "batch"
    assert statistics["matrix"]["cells"] == [
        {"x": float(x), "y": y, "count": 1} for x, y in zip(algorithms, strategies, strict=True)
    ]

    query = {**snapshot, "conditions": RULES, "limit": 1, "include_statistics": True}
    first = client.post(base + "/groups/query", json=query).json()
    second = client.post(base + "/groups/query", json={**query, "offset": 1}).json()
    students = client.get(base + "/students", params=snapshot).json()["items"]
    assert first["matched_count"] == 2 and first["total_count"] == first["eligible_count"] == 4
    assert first["ratio"] == 0.5 and first["page"]["has_more"]
    assert first["statistics"]["scope"] == {"kind": "matched_group", "record_count": 2}
    assert second["statistics"] == first["statistics"]
    assert first["items"] + second["items"] == students[:2]
    assert len({backgrounds[index] for index in (0, 1)}) == 2
    proof_id = students[1]["scores"][0]["evidence_id"]
    proof = client.get(base + "/evidence/" + proof_id, params=snapshot).json()
    assert proof["batch_id"] == batch["batch_id"] and proof["snapshot_id"] == batch["snapshot_id"]
    assert proof["student_id"] == students[1]["student_id"] and proof["score"] == 2.75
    assert proof["source"]["score_cell"] == "C9" and proof["comment_status"] == "withheld"
    rubric = client.get(base + "/rubric", params=snapshot).json()
    assert proof["rubric_criterion_id"] in {c["rubric_criterion_id"] for c in rubric["criteria"]}

    saved_response = save_result(client, base, batch, conditions=RULES, title="合成测评分析")
    assert saved_response.status_code == 201, saved_response.text
    saved = confirm(client, base, batch, saved_response.json())
    legacy = export_result(client, base, batch, saved).json()
    assert legacy["analysis"]["total_count"] == 4 and legacy["analysis"]["matched_count"] == 2
    workbook, text = read_report(export_result(client, base, batch, saved, format=format), format)
    assert "参评人数" in text and "测评平均分" in text and "本次测评" in text
    assert "不推定所属班级、学校或课程" in text
    assert "生成时间（协调世界时）" in text  # Explicitly a report timestamp, not assessment date.
    for invented in (
        *set(backgrounds),
        scope_label,
        "合成丁校丁班",
        "合成赛事",
        "合成比赛",
        "2025-03-04",
        "2024-02-03",
        "本班",
        "全班",
        "班级平均分",
        "课堂成绩",
        "教学核查",
    ):
        assert invented not in text
    if workbook:
        info = {
            row[0]: row[1] for row in workbook["测评概况"].iter_rows(values_only=True) if row[0]
        }
        assert info["参评人数"] == 4 and info["分析范围"] == "本次测评"
        row = next(
            row
            for row in workbook["维度统计"].iter_rows(values_only=True)
            if row[0] == "算法与数据结构"
        )
        assert row[1] == 4 and row[2] == 2.9375
        metrics = {
            row[0]: row[1] for row in workbook["选定群体"].iter_rows(values_only=True) if row[0]
        }
        assert metrics["符合条件人数"] == 2 and metrics["参评人数"] == 4
        workbook.close()
    else:
        assert "| 参评人数 | 4 |" in text and "| 算法与数据结构 | 4 | 2.94 |" in text
    public = json.dumps(
        [preview, batch, statistics, first, second, proof, rubric, legacy], ensure_ascii=False
    )
    for private in set(backgrounds):
        assert private not in public
    assert all(
        f'"{key}"' not in public
        for key in ("class_id", "class_name", "school_name", "competition_name")
    )
