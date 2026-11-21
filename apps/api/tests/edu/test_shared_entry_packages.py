"""B-03C：多 ZIP 含相同包内路径时的导入、来源绑定与回读。

全部使用合成答卷；覆盖同路径双包 HTTP 201、材料按来源包各存一条、
重启后按各自原件回查代码与测试点、跨包摘要错配拒绝、旧库升级不丢记录。
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.core.errors import APIError
from app.edu.assessments import AssessmentReader, AssessmentService, AssetInput
from app.edu.originals import OriginalStore
from app.importing.edu.answersheet import (
    AnswerSheetEvidence,
    merge_answersheets,
    parse_answersheet,
    tag_package,
)
from app.storage.database import Database
from edu.answersheet_samples import paper_html, zip_of

URL = "/api/v1/edu/assessments"
SHARED_ENTRY = "包/正式答卷/答卷.html"


def shared_path_packages() -> tuple[bytes, bytes]:
    """两个包内部路径完全相同，参与者、代码和测试点都不同。"""
    left = zip_of(
        {
            SHARED_ENTRY: paper_html(
                key="S001",
                name="周同学",
                code="int main(){ return 1; }",
                rows=[("1", "答案正确", "5", "1.00 ms", "300 KB")],
            )
        }
    )
    right = zip_of(
        {
            SHARED_ENTRY: paper_html(
                key="S002",
                name="李同学",
                code="int main(){ return 2; }",
                rows=[("1", "答案错误", "0", "9.00 ms", "900 KB")],
            )
        }
    )
    return left, right


def upload_shared(client) -> str:
    left, right = shared_path_packages()
    response = client.post(
        "".join([URL]),
        files=[("file", ("甲班.zip", left)), ("file", ("乙班.zip", right))],
    )
    assert response.status_code == 201, response.text
    return response.json()["assessment_id"]


def store_for(runtime) -> OriginalStore:
    return OriginalStore(runtime.db.path.parent / "originals")


def test_shared_internal_path_across_packages_imports_201(client, runtime):
    assessment_id = upload_shared(client)

    overview = client.get(f"{URL}/{assessment_id}").json()
    assets = overview["assets"]
    assert [asset["source_index"] for asset in assets] == [0, 1]
    assert assets[0]["sha256"] != assets[1]["sha256"]

    # 材料：同一包内路径，各存一条，各自绑自己的来源包
    materials = overview["materials"]
    assert [item["entry"] for item in materials] == [SHARED_ENTRY, SHARED_ENTRY]
    assert {item["asset_id"] for item in materials} == {asset["asset_id"] for asset in assets}

    # 学生与提交：各自绑自己的包，不串证据
    with runtime.db.transaction() as connection:
        participants = connection.execute(
            "SELECT external_key,source_asset_id FROM assessment_participants "
            "WHERE assessment_id=? ORDER BY external_key",
            (assessment_id,),
        ).fetchall()
        submissions = connection.execute(
            "SELECT source_asset_id,source_entry,code_text FROM submissions "
            "WHERE assessment_id=? ORDER BY code_text",
            (assessment_id,),
        ).fetchall()
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM assessment_diagnostics WHERE assessment_id=? "
                "AND code IN ('SOURCE_ENTRY_AMBIGUOUS','SOURCE_ENTRY_UNRESOLVED')",
                (assessment_id,),
            ).fetchone()[0]
            == 0
        )
    assert [row["source_asset_id"] for row in participants] == [
        asset["asset_id"] for asset in assets
    ]
    assert [row["source_asset_id"] for row in submissions] == [
        asset["asset_id"] for asset in assets
    ]
    assert all(row["source_entry"] == SHARED_ENTRY for row in submissions)


def test_shared_path_evidence_and_originals_survive_restart(client, client_factory, runtime):
    assessment_id = upload_shared(client)
    overview = client.get(f"{URL}/{assessment_id}").json()
    snapshot = overview["source_snapshot_id"]
    assets = {asset["source_index"]: asset for asset in overview["assets"]}

    left, right = shared_path_packages()
    assert assets[0]["sha256"] == hashlib.sha256(left).hexdigest()
    assert assets[1]["sha256"] == hashlib.sha256(right).hexdigest()

    # 关闭已有应用：全新的数据库对象、原件存储对象、重新创建的应用
    reader = AssessmentReader(Database(runtime.db.path), originals=store_for(runtime))
    for index, payload in ((0, left), (1, right)):
        original = reader.original(runtime.p, assessment_id, assets[index]["asset_id"])
        assert original.payload == payload
        assert original.sha256 == assets[index]["sha256"]
        assert original.source_snapshot_id == snapshot

        # 原件重新解析的结果必须与库里绑到同一个包的证据逐字段一致
        reparsed = tag_package(parse_answersheet(original.payload), index)
        submission = reparsed.submissions[0]
        with runtime.db.transaction() as connection:
            participant = connection.execute(
                "SELECT student_pk FROM assessment_participants "
                "WHERE assessment_id=? AND external_key=?",
                (assessment_id, submission.participant_key),
            ).fetchone()
            stored = connection.execute(
                "SELECT submission_id,code_text,source_asset_id,source_index FROM submissions "
                "WHERE assessment_id=? AND student_pk=?",
                (assessment_id, participant["student_pk"]),
            ).fetchone()
            observation = connection.execute(
                "SELECT status,source_row FROM test_observations WHERE submission_id=?",
                (stored["submission_id"],),
            ).fetchone()
        assert stored["code_text"] == submission.code
        assert stored["source_asset_id"] == assets[index]["asset_id"]
        assert stored["source_index"] == submission.source_index
        assert observation["source_row"] == reparsed.observations[0].source_row
        assert observation["status"] == reparsed.observations[0].status

    with client_factory(runtime.token) as restarted:
        again = restarted.get(f"{URL}/{assessment_id}").json()
        assert again["source_snapshot_id"] == snapshot
        assert len(again["materials"]) == 2
        items = restarted.get(f"{URL}/{assessment_id}/students").json()["items"]
        for index in (0, 1):
            payload_sha = assets[index]["sha256"]
            pk = items[index]["student_pk"]
            detail = restarted.get(
                f"{URL}/{assessment_id}/students/{pk}",
                params={"asset_sha256": payload_sha, "source_snapshot_id": snapshot},
            )
            assert detail.status_code == 200, detail.text


def test_shared_path_digest_from_other_package_is_rejected(client, runtime):
    assessment_id = upload_shared(client)
    overview = client.get(f"{URL}/{assessment_id}").json()
    left_asset, right_asset = overview["assets"][0], overview["assets"][1]
    items = client.get(f"{URL}/{assessment_id}/students").json()["items"]
    left_student = items[0]["student_pk"]

    ok = client.get(
        f"{URL}/{assessment_id}/students/{left_student}",
        params={"asset_sha256": left_asset["sha256"]},
    )
    assert ok.status_code == 200

    # 同批次里另一个包的摘要不能证明这个学生的证据来源
    mismatch = client.get(
        f"{URL}/{assessment_id}/students/{left_student}",
        params={"asset_sha256": right_asset["sha256"]},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "SOURCE_CHANGED"


def test_shared_path_duplicate_reupload_is_idempotent(client, runtime):
    # 复用同一份字节：zip 内时间戳会随构造时刻变化，重建出的包不算同一份文件
    left, right = shared_path_packages()
    first = client.post(URL, files=[("file", ("甲班.zip", left)), ("file", ("乙班.zip", right))])
    assert first.status_code == 201, first.text
    assessment_id = first.json()["assessment_id"]
    again = client.post(URL, files=[("file", ("甲班.zip", left)), ("file", ("乙班.zip", right))])
    assert again.status_code in (200, 201), again.text
    assert again.json()["duplicate"] is True
    assert again.json()["assessment_id"] == assessment_id
    with runtime.db.transaction() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM assessment_materials WHERE assessment_id=?",
                (assessment_id,),
            ).fetchone()[0]
            == 2
        )


def test_material_rows_are_package_scoped_after_migration(client, runtime):
    """迁移后同一 (assessment_id, entry) 允许来自不同包的两条记录。"""
    assessment_id = upload_shared(client)
    with runtime.db.transaction(write=True) as connection:
        first = connection.execute(
            "SELECT asset_id FROM assessment_assets WHERE assessment_id=? ORDER BY source_index",
            (assessment_id,),
        ).fetchone()
        # 同一来源包内重复登记同一路径属于重复，仍要被拒
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO assessment_materials (material_id,assessment_id,entry,"
                "category,basis,signals,needs_confirmation,asset_id) VALUES (?,?,?,?,?,?,?,?)",
                (
                    "mig-dup",
                    assessment_id,
                    SHARED_ENTRY,
                    "STUDENT_PAPER",
                    "path",
                    "[]",
                    0,
                    first["asset_id"],
                ),
            )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM assessment_materials WHERE assessment_id=?",
                (assessment_id,),
            ).fetchone()[0]
            == 2
        )


MIGRATIONS = Path(__file__).resolve().parents[2] / "app" / "storage" / "migrations"


def _build_database_before(path: Path, last: str) -> None:
    """按 001..last 顺序手工建库，模拟已上线但没有 013 的历史数据库。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT"
    )
    for file in sorted(MIGRATIONS.glob("*.sql")):
        if file.name > last:
            break
        sql = file.read_text(encoding="utf-8")
        pending = ""
        for line in sql.splitlines(keepends=True):
            pending += line
            if sqlite3.complete_statement(pending):
                connection.execute(pending)
                pending = ""
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?,?,?)",
            (file.name, hashlib.sha256(sql.encode()).hexdigest(), "2026-11-20T00:00:00Z"),
        )
    connection.commit()
    connection.close()


@pytest.mark.parametrize(
    "last_migration",
    ["009_evidence_provenance.sql", "012_original_artifacts.sql"],
    ids=["pre-012", "pre-013"],
)
def test_upgrade_keeps_material_rows_and_relaxes_scope(tmp_path, last_migration):
    path = tmp_path / "legacy.sqlite3"
    _build_database_before(path, last_migration)

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "INSERT INTO teachers VALUES ('t-legacy','issuer','subject',1,'2026-11-01T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO data_scopes VALUES ('s-legacy','历史范围','2026-11-01T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO assessments (assessment_id,data_scope_id,owner_teacher_id,title,course,"
        "assessment_date,file_sha256,parser_version,student_count,problem_count,"
        "submission_count,observation_count,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "a-legacy",
            "s-legacy",
            "t-legacy",
            "历史批次",
            None,
            None,
            "a" * 64,
            "answersheet.v1",
            1,
            1,
            1,
            1,
            "2026-11-01T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO assessment_assets (asset_id,assessment_id,entry,kind,sha256,byte_size,"
        "source_index,created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("a-legacy-asset", "a-legacy", "历史包.zip", "zip-html", "b" * 64, 10, 0, "x"),
    )
    connection.execute(
        "INSERT INTO assessment_materials (material_id,assessment_id,entry,category,basis,"
        "signals,needs_confirmation,asset_id) VALUES (?,?,?,?,?,?,?,?)",
        (
            "a-legacy-mat",
            "a-legacy",
            SHARED_ENTRY,
            "STUDENT_PAPER",
            "path",
            "[]",
            0,
            "a-legacy-asset",
        ),
    )
    connection.execute(
        "INSERT INTO assessment_materials (material_id,assessment_id,entry,category,basis,"
        "signals,needs_confirmation,asset_id) VALUES (?,?,?,?,?,?,?,?)",
        ("a-legacy-unbound", "a-legacy", "包/题目.html", "UNKNOWN", "unknown", "[]", 1, None),
    )
    connection.commit()
    connection.close()

    Database(path).migrate()

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT storage_state FROM assessment_assets WHERE asset_id='a-legacy-asset'"
    ).fetchone() == ("absent",)
    assert connection.execute(
        "SELECT source_snapshot_id FROM assessments WHERE assessment_id='a-legacy'"
    ).fetchone() == (None,)
    assert {
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations WHERE version >= '009' ORDER BY version"
        )
    } == {
        "009_evidence_provenance.sql",
        "012_original_artifacts.sql",
        "013_material_package_scope.sql",
    }
    rows = connection.execute(
        "SELECT material_id,entry,asset_id FROM assessment_materials ORDER BY material_id"
    ).fetchall()
    assert rows == [
        ("a-legacy-mat", SHARED_ENTRY, "a-legacy-asset"),
        ("a-legacy-unbound", "包/题目.html", None),
    ]

    # 升级后同一包内路径可以来自另一个来源包，同一包内重复仍然被拒
    connection.execute(
        "INSERT INTO assessment_assets (asset_id,assessment_id,entry,kind,sha256,byte_size,"
        "source_index,created_at,storage_state) VALUES (?,?,?,?,?,?,?,?,?)",
        ("a-legacy-asset-2", "a-legacy", "历史包2.zip", "zip-html", "c" * 64, 10, 1, "x", "absent"),
    )
    connection.execute(
        "INSERT INTO assessment_materials (material_id,assessment_id,entry,category,basis,"
        "signals,needs_confirmation,asset_id) VALUES (?,?,?,?,?,?,?,?)",
        (
            "a-legacy-mat-2",
            "a-legacy",
            SHARED_ENTRY,
            "STUDENT_PAPER",
            "path",
            "[]",
            0,
            "a-legacy-asset-2",
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO assessment_materials (material_id,assessment_id,entry,category,basis,"
            "signals,needs_confirmation,asset_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                "a-legacy-mat-3",
                "a-legacy",
                SHARED_ENTRY,
                "STUDENT_PAPER",
                "path",
                "[]",
                0,
                "a-legacy-asset-2",
            ),
        )
    connection.commit()
    connection.close()


def _package_assets(payloads: tuple[bytes, ...]) -> list[AssetInput]:
    assets = []
    for index, payload in enumerate(payloads):
        document = parse_answersheet(payload)
        assets.append(
            AssetInput(
                entry=f"第{index + 1}包.zip",
                kind="zip-html",
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload),
                source_index=index,
                entries=frozenset(item.entry for item in document.materials),
            )
        )
    return assets


def test_unresolvable_material_source_returns_business_error(runtime):
    """没有包号的旧调用方碰上同路径双包：明确的业务错误，不是 503，也不绑错包。"""
    payloads = shared_path_packages()
    evidence = merge_answersheets([parse_answersheet(payload) for payload in payloads])
    service = AssessmentService(runtime.db)
    with pytest.raises(APIError) as excinfo:
        service.create_answersheet(
            runtime.p,
            b"",
            data_scope_id=runtime.scope,
            title="同路径双包",
            course=None,
            assessment_date=None,
            evidence=evidence,
            file_sha256="e" * 64,
            assets=_package_assets(payloads),
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "SOURCE_AMBIGUOUS"
    with runtime.db.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM assessment_materials").fetchone()[0] == 0


def test_duplicate_material_in_same_package_returns_business_error(runtime):
    payload = shared_path_packages()[0]
    document = parse_answersheet(payload)
    duplicated = tag_package(document, 0)
    evidence = AnswerSheetEvidence(
        students=duplicated.students,
        problems=duplicated.problems,
        submissions=duplicated.submissions,
        observations=duplicated.observations,
        materials=duplicated.materials + duplicated.materials,
        skipped=duplicated.skipped,
        diagnostics=duplicated.diagnostics,
        order_basis=duplicated.order_basis,
    )
    service = AssessmentService(runtime.db)
    with pytest.raises(APIError) as excinfo:
        service.create_answersheet(
            runtime.p,
            payload,
            data_scope_id=runtime.scope,
            title="同包重复材料",
            course=None,
            assessment_date=None,
            evidence=evidence,
            file_sha256=hashlib.sha256(payload).hexdigest(),
            assets=_package_assets((payload,)),
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "SOURCE_BINDING_CONFLICT"
