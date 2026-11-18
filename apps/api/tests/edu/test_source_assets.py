"""上传成功后必须留下一条来源资产记录（V1.4 的 SourceAsset 最小实现）。"""

from hashlib import sha256

from app.importing.edu import parse_excel


def test_preview_records_source_asset(client, runtime, book, pack):
    runtime.service.parser = parse_excel
    raw = pack(book)

    response = client.post(
        "/api/v1/edu/imports/preview",
        files={"file": ("合成测评.xlsx", raw, "application/octet-stream")},
    )

    assert response.status_code == 201, response.text
    with runtime.db.transaction() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM source_assets")]

    assert len(rows) == 1
    asset = rows[0]
    assert asset["kind"] == "xlsx"
    assert asset["byte_size"] == len(raw)
    assert asset["file_sha256"] == sha256(raw).hexdigest()
    assert asset["import_id"] == response.json()["import"]["import_id"]
