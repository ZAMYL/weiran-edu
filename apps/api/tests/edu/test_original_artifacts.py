"""B-03B：原件保存、来源快照与可信回读。

全部使用合成答卷；覆盖单包/多包、重启一致性、篡改与删除、
跨测评跨用户拒绝、同批次摘要错配、保存失败回滚、旧批次兼容。
"""

import hashlib

import pytest

from app.core.errors import APIError
from app.edu.assessments import (
    AssessmentReader,
    AssessmentService,
    AssetInput,
    _source_snapshot_id,
)
from app.edu.originals import OriginalStore
from app.importing.edu.answersheet import parse_answersheet
from app.storage.database import Database
from edu.answersheet_samples import paper_html, simple_zip, zip_of

URL = "/api/v1/edu/assessments"


def upload(client, *packages: tuple[str, bytes]) -> str:
    files = [("file", (name, payload)) for name, payload in packages]
    response = client.post(URL, files=files)
    assert response.status_code == 201, response.text
    return response.json()["assessment_id"]


def store_for(runtime) -> OriginalStore:
    return OriginalStore(runtime.db.path.parent / "originals")


def student_pk(client, assessment_id: str, index: int = 0) -> str:
    return client.get(f"{URL}/{assessment_id}/students").json()["items"][index]["student_pk"]


def test_single_zip_original_survives_restart_and_matches_evidence(client, client_factory, runtime):
    payload = simple_zip(code="int main(){ puts(\"ok\"); }")
    assessment_id = upload(client, ("竞赛.zip", payload))

    overview = client.get(f"{URL}/{assessment_id}").json()
    asset = overview["assets"][0]
    snapshot = overview["source_snapshot_id"]
    assert asset["storage_state"] == "stored"
    assert asset["sha256"] == hashlib.sha256(payload).hexdigest()
    assert len(snapshot) == 64

    # 原件确实落在数据库同级的受控目录，而不是仓库里
    stored = store_for(runtime).path_for(asset["sha256"])
    assert stored.is_file()
    assert stored.read_bytes() == payload

    # 重启：全新的 Database / OriginalStore / 应用，读同一个文件
    reopened = AssessmentReader(Database(runtime.db.path), originals=store_for(runtime))
    original = reopened.original(runtime.p, assessment_id, asset["asset_id"])
    assert original.payload == payload
    assert original.sha256 == asset["sha256"]
    assert original.source_snapshot_id == snapshot

    with client_factory(runtime.token) as restarted:
        again = restarted.get(f"{URL}/{assessment_id}").json()
        assert again["source_snapshot_id"] == snapshot
        pk = student_pk(restarted, assessment_id)
        detail = restarted.get(
            f"{URL}/{assessment_id}/students/{pk}",
            params={"asset_sha256": asset["sha256"], "source_snapshot_id": snapshot},
        )
        assert detail.status_code == 200, detail.text

    # 按源文件与题块位置回查：原件重新解析的结果必须与库里证据逐字段一致
    reparsed = parse_answersheet(original.payload)
    submission = detail.json()["submissions"][0]
    source_submission = next(
        item
        for item in reparsed.submissions
        if item.problem_label == submission["problem_label"]
        and item.source_entry == submission["source"]["entry"]
    )
    assert source_submission.code == submission["code"]
    parsed_rows = [
        item
        for item in reparsed.observations
        if item.participant_key == source_submission.participant_key
        and item.problem_label == source_submission.problem_label
    ]
    assert [row.status for row in parsed_rows] == [
        item["status"] for item in submission["observations"]
    ]
    assert [row.source_row for row in parsed_rows] == [
        item["source_row"] for item in submission["observations"]
    ]


def test_multi_zip_originals_and_snapshot_are_stable(client, client_factory, runtime):
    left = zip_of({"包/正式答卷/S001.html": paper_html(key="S001", name="甲同学")})
    right = zip_of({"包/正式答卷/S002.html": paper_html(key="S002", name="乙同学")})
    assessment_id = upload(client, ("甲班.zip", left), ("乙班.zip", right))

    overview = client.get(f"{URL}/{assessment_id}").json()
    assert [asset["entry"] for asset in overview["assets"]] == ["甲班.zip", "乙班.zip"]
    assert all(asset["storage_state"] == "stored" for asset in overview["assets"])
    snapshot = overview["source_snapshot_id"]

    reader = AssessmentReader(Database(runtime.db.path), originals=store_for(runtime))
    payloads = {
        asset["entry"]: reader.original(runtime.p, assessment_id, asset["asset_id"]).payload
        for asset in overview["assets"]
    }
    assert payloads == {"甲班.zip": left, "乙班.zip": right}

    with client_factory(runtime.token) as restarted:
        assert restarted.get(f"{URL}/{assessment_id}").json()["source_snapshot_id"] == snapshot


def test_other_package_digest_cannot_prove_this_students_evidence(client):
    """同一批次里 A 包的摘要不能用来证明 B 包中的代码与测试点。"""
    left = zip_of({"包/正式答卷/S001.html": paper_html(key="S001", name="甲同学")})
    right = zip_of(
        {
            "包/正式答卷/S002.html": paper_html(
                key="S002", name="乙同学", code="int main(){ return 1; }"
            )
        }
    )
    assessment_id = upload(client, ("甲班.zip", left), ("乙班.zip", right))
    assets = client.get(f"{URL}/{assessment_id}").json()["assets"]
    digest = {asset["entry"]: asset["sha256"] for asset in assets}
    students = client.get(f"{URL}/{assessment_id}/students").json()["items"]
    by_key = {item["external_key"]: item["student_pk"] for item in students}

    url = f"{URL}/{assessment_id}/students/{by_key['S002']}"
    mismatched = client.get(url, params={"asset_sha256": digest["甲班.zip"]})
    assert mismatched.status_code == 409
    assert mismatched.json()["error"]["code"] == "SOURCE_CHANGED"

    matched = client.get(url, params={"asset_sha256": digest["乙班.zip"]})
    assert matched.status_code == 200, matched.text
    assert matched.json()["submissions"][0]["source"]["asset_sha256"] == digest["乙班.zip"]


def test_snapshot_mismatch_is_rejected(client):
    assessment_id = upload(client, ("竞赛.zip", simple_zip()))
    pk = student_pk(client, assessment_id)
    response = client.get(
        f"{URL}/{assessment_id}/students/{pk}", params={"source_snapshot_id": "0" * 64}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_CHANGED"


def test_tampered_deleted_and_revoked_originals_are_refused(client, runtime):
    payload = simple_zip()
    assessment_id = upload(client, ("竞赛.zip", payload))
    asset = client.get(f"{URL}/{assessment_id}").json()["assets"][0]
    reader = AssessmentReader(Database(runtime.db.path), originals=store_for(runtime))
    path = store_for(runtime).path_for(asset["sha256"])

    path.write_bytes(payload + b"tampered")
    with pytest.raises(APIError) as tampered:
        reader.original(runtime.p, assessment_id, asset["asset_id"])
    assert tampered.value.status_code == 409
    assert tampered.value.code == "SOURCE_CHANGED"

    path.unlink()
    with pytest.raises(APIError) as missing:
        reader.original(runtime.p, assessment_id, asset["asset_id"])
    assert missing.value.code == "ORIGINAL_UNAVAILABLE"

    path.write_bytes(payload)
    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE assessment_assets SET storage_state='revoked' WHERE asset_id=?",
            (asset["asset_id"],),
        )
    with pytest.raises(APIError) as revoked:
        reader.original(runtime.p, assessment_id, asset["asset_id"])
    assert revoked.value.code == "ORIGINAL_UNAVAILABLE"


def test_cross_user_and_cross_assessment_original_access_is_denied(
    client, client_factory, runtime
):
    first = upload(client, ("甲.zip", simple_zip()))
    second = upload(
        client, ("乙.zip", zip_of({"包/正式答卷/S009.html": paper_html(key="S009", name="丙同学")}))
    )
    asset = client.get(f"{URL}/{first}").json()["assets"][0]
    reader = AssessmentReader(Database(runtime.db.path), originals=store_for(runtime))

    with pytest.raises(APIError) as other_teacher:
        reader.original(runtime.q, first, asset["asset_id"])
    assert other_teacher.value.status_code == 404

    with pytest.raises(APIError) as other_assessment:
        reader.original(runtime.p, second, asset["asset_id"])
    assert other_assessment.value.status_code == 404

    with client_factory(runtime.other_token) as other_client:
        assert other_client.get(f"{URL}/{first}").status_code == 404
        pk = student_pk(client, first)
        assert other_client.get(f"{URL}/{first}/students/{pk}").status_code == 404


def test_original_write_failure_rolls_back_assessment_and_files(runtime, monkeypatch):
    left = simple_zip()
    right = zip_of({"包/正式答卷/S002.html": paper_html(key="S002", name="乙同学")})
    evidence = parse_answersheet(left)
    store = store_for(runtime)
    service = AssessmentService(runtime.db, originals=store)
    assets = [
        AssetInput(
            entry="甲.zip",
            kind="zip_html",
            sha256=hashlib.sha256(left).hexdigest(),
            byte_size=len(left),
            source_index=0,
            entries=frozenset(item.entry for item in evidence.materials),
            payload=left,
        ),
        AssetInput(
            entry="乙.zip",
            kind="zip_html",
            sha256=hashlib.sha256(right).hexdigest(),
            byte_size=len(right),
            source_index=1,
            payload=right,
        ),
    ]
    real_put = store.put
    calls = {"n": 0}

    def flaky(sha256, payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise APIError(409, "ORIGINAL_WRITE_FAILED", "磁盘写入失败")
        return real_put(sha256, payload)

    monkeypatch.setattr(store, "put", flaky)
    with pytest.raises(APIError):
        service.create_answersheet(
            runtime.p,
            b"",
            data_scope_id=runtime.scope,
            title="写入失败批次",
            course=None,
            assessment_date=None,
            evidence=evidence,
            file_sha256="f" * 64,
            assets=assets,
        )

    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM assessment_assets").fetchone()[0] == 0
    assert list(store.root.rglob("*.zip")) == []
    assert list(store.root.rglob("*.part")) == []


def test_store_never_overwrites_and_rejects_digest_mismatch(tmp_path):
    store = OriginalStore(tmp_path / "originals")
    payload = simple_zip()
    digest = hashlib.sha256(payload).hexdigest()

    assert store.put(digest, payload) is True
    assert store.put(digest, payload) is False

    with pytest.raises(APIError) as mismatch:
        store.put(digest, b"different bytes")
    assert mismatch.value.status_code == 422

    store.path_for(digest).write_bytes(b"corrupted")
    with pytest.raises(APIError) as conflict:
        store.put(digest, payload)
    assert conflict.value.code == "ORIGINAL_CONTENT_CONFLICT"
    assert store.path_for(digest).read_bytes() == b"corrupted"


def test_legacy_batch_without_originals_still_reads_structured_evidence(runtime):
    payload = simple_zip()
    evidence = parse_answersheet(payload)
    digest = hashlib.sha256(payload).hexdigest()
    service = AssessmentService(runtime.db)  # 未配置原件存储：旧行为
    summary = service.create_answersheet(
        runtime.p,
        payload,
        data_scope_id=runtime.scope,
        title="旧批次",
        course=None,
        assessment_date=None,
        evidence=evidence,
        assets=[
            AssetInput(
                entry="旧.zip",
                kind="zip_html",
                sha256=digest,
                byte_size=len(payload),
                source_index=0,
                entries=frozenset(item.entry for item in evidence.materials),
                payload=payload,
            )
        ],
    )
    reader = AssessmentReader(Database(runtime.db.path), originals=store_for(runtime))
    overview = reader.overview(runtime.p, summary.assessment_id)
    assert overview.assets[0].storage_state == "absent"
    assert overview.source_snapshot_id == _source_snapshot_id(
        summary.assessment_id,
        [
            AssetInput(
                entry="旧.zip",
                kind="zip_html",
                sha256=digest,
                byte_size=len(payload),
                source_index=0,
            )
        ],
    )

    participant = reader.participants(runtime.p, summary.assessment_id).items[0]
    detail = reader.student(
        runtime.p, summary.assessment_id, participant.student_pk, asset_sha256=digest
    )
    assert detail.submissions[0].code == evidence.submissions[0].code

    with pytest.raises(APIError) as unavailable:
        reader.original(runtime.p, summary.assessment_id, overview.assets[0].asset_id)
    assert unavailable.value.code == "ORIGINAL_UNAVAILABLE"
