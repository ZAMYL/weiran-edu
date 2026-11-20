"""B-03：统计可信性。未知、未作答、部分正确都不得被算作失败。"""

from edu.answersheet_samples import paper_html, simple_zip, zip_of

URL = "/api/v1/edu/assessments"


def upload(client, payload: bytes) -> str:
    response = client.post(URL, files={"file": ("竞赛.zip", payload)})
    assert response.status_code == 201, response.text
    return response.json()["assessment_id"]


def pattern_of(client, assessment_id: str) -> dict:
    response = client.get(f"{URL}/{assessment_id}/patterns")
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


def test_unknown_partial_and_unattempted_are_not_failures(client):
    payload = simple_zip(
        rows=[
            ("1", "答案正确", "5", "2.00 ms", "304 KB"),
            ("2", "答案错误", "0", "3.00 ms", "304 KB"),
            ("3", "部分正确", "2", "4.00 ms", "304 KB"),
            ("4", "等待评测", "", "5.00 ms", "304 KB"),
        ]
    )
    pattern = pattern_of(client, upload(client, payload))

    assert pattern["pass_count"] == 1
    assert pattern["fail_count"] == 1
    assert pattern["partial_count"] == 1
    assert pattern["unknown_count"] == 1
    assert pattern["observation_count"] == 4
    # 分母是"有明确结论"的 3 条，不是全部 4 条
    assert pattern["effective_count"] == 3
    assert pattern["failure_count"] == 1
    assert pattern["failure_rate"] == 0.3333
    assert pattern["coverage"] == 0.75
    assert pattern["unknown_rate"] == 0.25
    # 唯一失败点是 T2；未知状态的 T4 不得进入失败榜
    assert [item["test_index"] for item in pattern["worst_test_points"]] == [2]


def test_only_unattempted_observations_have_no_failure_rate(client):
    payload = simple_zip(
        rows=[
            ("1", "未作答", "0", "0 ms", "0 KB"),
            ("2", "未作答", "0", "0 ms", "0 KB"),
        ]
    )
    pattern = pattern_of(client, upload(client, payload))

    assert pattern["not_attempted_count"] == 2
    assert pattern["effective_count"] == 0
    assert pattern["failure_count"] == 0
    assert pattern["failure_rate"] is None
    assert pattern["worst_test_points"] == []


def test_conflicting_full_marks_disable_score_rate(client):
    payload = zip_of(
        {
            "包/正式答卷/S001.html": paper_html(ratio="5 / 5"),
            "包/正式答卷/S002.html": paper_html(
                key="S002", name="吕同学", verdict="答案错误", ratio="0 / 10"
            ),
        }
    )
    assessment_id = upload(client, payload)

    pattern = pattern_of(client, assessment_id)
    assert pattern["score_rate_available"] is False
    assert pattern["average_score"] is None

    overview = client.get(f"{URL}/{assessment_id}").json()
    assert overview["review_state"] == "needs_review"
    assert "PROBLEM_TOTAL_SCORE_CONFLICT" in overview["review_reasons"]
    assert overview["counts_provisional"] is True

    report = client.get(f"{URL}/{assessment_id}/report").text
    assert "满分冲突" in report
    assert "待人工确认" in report


def test_mixed_participant_prefixes_are_provisional(client):
    payload = zip_of(
        {
            "包/正式答卷/S001.html": paper_html(),
            "包/正式答卷/X001.html": paper_html(key="X001", name="测试账号"),
        }
    )
    assessment_id = upload(client, payload)

    overview = client.get(f"{URL}/{assessment_id}").json()
    assert overview["review_state"] == "needs_review"
    assert "PARTICIPANT_SERIES_MIXED" in overview["review_reasons"]
    assert overview["student_count"] == 2
    assert overview["counts_provisional"] is True

    dimensions = client.get(f"{URL}/{assessment_id}/dimensions").json()
    assert dimensions["review_state"] == "needs_review"


def test_failed_test_point_filter_ignores_unknown_status(client):
    payload = zip_of(
        {
            "包/正式答卷/S001.html": paper_html(
                rows=[
                    ("1", "等待评测", "", "2.00 ms", "304 KB"),
                    ("2", "答案正确", "5", "1 ms", "1 KB"),
                ]
            ),
            "包/正式答卷/S002.html": paper_html(
                key="S002",
                name="吕同学",
                verdict="答案错误",
                ratio="0 / 5",
                rows=[("1", "答案错误", "0", "2.00 ms", "304 KB")],
            ),
        }
    )
    assessment_id = upload(client, payload)

    response = client.get(f"{URL}/{assessment_id}/problems/7-1/students", params={"test_index": 1})
    assert response.status_code == 200
    body = response.json()
    # S001 的 T1 状态未知，不能算作"失败测试点"而被筛出来
    assert [item["external_key"] for item in body["items"]] == ["S002"]
