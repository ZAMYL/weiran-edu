"""B-03：可信证据持久化与来源回读。

覆盖：资产/材料/诊断落库、字段级对账、关库重开后的回读、
越权与来源变化时的拒绝、观测引用不一致时整批回滚。
"""

import dataclasses
import hashlib

import pytest

from app.core.errors import APIError
from app.edu.assessments import AssessmentReader, AssessmentService
from app.importing.edu.answersheet import ObservationRecord, parse_answersheet
from app.storage.database import Database
from edu.answersheet_samples import paper_html, simple_zip, zip_of

URL = "/api/v1/edu/assessments"


def upload(client, payload: bytes, name: str = "竞赛.zip") -> str:
    response = client.post(URL, files={"file": (name, payload)})
    assert response.status_code == 201, response.text
    return response.json()["assessment_id"]


def test_upload_records_assets_materials_and_diagnostics(client):
    payload = zip_of(
        {
            "包/正式答卷/S001.html": paper_html(),
            "包/标准答案/A001.html": paper_html(key="A001", name="参考答案", paper_type="标准答案"),
        }
    )
    assessment_id = upload(client, payload)

    overview = client.get(f"{URL}/{assessment_id}").json()

    assert len(overview["assets"]) == 1
    asset = overview["assets"][0]
    assert asset["sha256"] == hashlib.sha256(payload).hexdigest()
    assert asset["entry"] == "竞赛.zip"
    assert asset["byte_size"] == len(payload)
    categories = {material["entry"]: material["category"] for material in overview["materials"]}
    assert categories["包/正式答卷/S001.html"] == "STUDENT_PAPER"
    assert categories["包/标准答案/A001.html"] == "STANDARD_ANSWER"
    assert overview["order_basis"] in {"SOURCE", "NATURAL_SORT"}
    assert "diagnostics" in overview


def test_fields_match_parser_and_survive_database_reopen(client, runtime):
    payload = simple_zip()
    assessment_id = upload(client, payload)
    evidence = parse_answersheet(payload)
    parsed_submission = evidence.submissions[0]
    parsed_observation = evidence.observations[0]

    # 模拟进程重启：用同一个文件换一个全新的 Database 实例再读。
    reader = AssessmentReader(Database(runtime.db.path))
    participants = reader.participants(runtime.p, assessment_id)
    assert participants.items[0].started_at == "2026-11-01 09:00:00"
    assert participants.items[0].finished_at == "2026-11-01 10:30:00"
    assert participants.items[0].display_name == "周同学"

    detail = reader.student(runtime.p, assessment_id, participants.items[0].student_pk)
    submission = detail.submissions[0]
    assert submission.code == parsed_submission.code
    assert submission.code_state == parsed_submission.code_state
    assert submission.code_truncated == parsed_submission.code_truncated
    assert submission.compiler_output == parsed_submission.compiler_output
    assert submission.source is not None
    assert submission.source.entry == parsed_submission.source_entry
    assert submission.source.source_index == parsed_submission.source_index
    assert submission.source.asset_sha256 == hashlib.sha256(payload).hexdigest()

    observation = submission.observations[0]
    assert observation.status == parsed_observation.status
    assert observation.status_class == "PASS"
    assert observation.status_known is True
    assert observation.duration_us == 2000
    assert observation.duration_text == "2.00 ms"
    assert observation.memory_text == "304 KB"
    assert observation.source_row == parsed_observation.source_row


def test_fractional_milliseconds_survive_persistence(client):
    payload = simple_zip(rows=[("1", "答案正确", "5", "0.5 ms", "304 KB")])
    assessment_id = upload(client, payload)
    student_pk = client.get(f"{URL}/{assessment_id}/students").json()["items"][0]["student_pk"]

    detail = client.get(f"{URL}/{assessment_id}/students/{student_pk}").json()
    observation = detail["submissions"][0]["observations"][0]

    # 0.5 ms 不再被取整吞掉：微秒值与原文都在，展示值四舍五入
    assert observation["duration_us"] == 500
    assert observation["duration_text"] == "0.5 ms"
    assert observation["duration_ms"] == 1


def test_other_teacher_and_changed_source_are_rejected(client, client_factory, runtime):
    payload = simple_zip()
    assessment_id = upload(client, payload)
    student_pk = client.get(f"{URL}/{assessment_id}/students").json()["items"][0]["student_pk"]

    with client_factory(runtime.other_token) as other:
        assert other.get(f"{URL}/{assessment_id}").status_code == 404
        assert other.get(f"{URL}/{assessment_id}/students/{student_pk}").status_code == 404

    detail_url = f"{URL}/{assessment_id}/students/{student_pk}"
    mismatched = client.get(detail_url, params={"asset_sha256": "0" * 64})
    assert mismatched.status_code == 409
    assert mismatched.json()["error"]["code"] == "SOURCE_CHANGED"

    matched = client.get(detail_url, params={"asset_sha256": hashlib.sha256(payload).hexdigest()})
    assert matched.status_code == 200


def test_observation_without_submission_rolls_back(client, runtime):
    payload = simple_zip()
    evidence = parse_answersheet(payload)
    orphan = ObservationRecord(
        participant_key="不存在的学生",
        problem_label="7-1",
        test_index=1,
        status="答案正确",
        score=5,
        duration_ms=1.0,
        memory_kb=100,
        source_row=0,
    )
    tampered = dataclasses.replace(evidence, observations=(*evidence.observations, orphan))
    service = AssessmentService(runtime.db)

    with pytest.raises(APIError) as caught:
        service.create_answersheet(
            runtime.p,
            payload,
            data_scope_id=runtime.scope,
            title="异常批次",
            course=None,
            assessment_date=None,
            evidence=tampered,
        )
    assert caught.value.status_code == 422
    assert caught.value.code == "ANSWERSHEET_INCONSISTENT"

    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM test_observations").fetchone()[0] == 0
