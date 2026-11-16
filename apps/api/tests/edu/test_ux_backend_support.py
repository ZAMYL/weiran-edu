"""Identity and number formatting that the pages rely on."""

from .conftest import commit


def test_me_reports_identity_kind_and_data_label(client, runtime):
    body = client.get("/api/v1/edu/me").json()
    assert body["teacher_id"] == runtime.teacher
    assert body["issuer"] == "local-pilot"
    assert body["subject"] == "synthetic-teacher"
    assert body["identity_kind"] == "demo_sample"
    assert body["data_label"] == "合成演示数据"


def test_student_scores_carry_a_two_decimal_display(client, runtime, stage):
    committed = commit(runtime, stage())
    url = f"/api/v1/edu/batches/{committed.batch_id}/students"
    response = client.get(url)
    assert response.status_code == 200, response.text
    scores = [score for student in response.json()["items"] for score in student["scores"]]
    assert scores
    for score in scores:
        assert score["display"] == f"{score['score']:.2f}"


def test_first_use_identity_is_distinguished(client_factory, runtime):
    first_use = runtime.admin.create_teacher("first-use-owner")
    token = runtime.admin.issue_token(first_use, hours=1)
    with client_factory(token) as scoped:
        body = scoped.get("/api/v1/edu/me").json()
    assert body["identity_kind"] == "demo_first_use"
    assert body["data_label"].startswith("合成演示数据")
