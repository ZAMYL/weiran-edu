"""Teacher confirmation never removes the system's evidence status or limitations."""

from io import BytesIO

import pytest
from openpyxl import load_workbook

from .test_discovery_api import custom_batch as custom_batch
from .test_discovery_api import query
from .test_discovery_results import exported, save


def document_text(response, format):
    assert response.status_code == 200, response.text[:200]
    if format == "md":
        return response.text.split("## 技术附录")[0]
    workbook = load_workbook(BytesIO(response.content))
    try:
        assert all(c.data_type != "f" for sheet in workbook for row in sheet for c in row)
        return "\n".join(
            str(c.value)
            for sheet in workbook
            if sheet.title != "技术附录"
            for row in sheet
            for c in row
            if c.value is not None
        )
    finally:
        workbook.close()


@pytest.mark.parametrize("format", ["md", "xlsx"])
@pytest.mark.parametrize("repetitions", [1, 20])
def test_reports_keep_system_evidence_status_separate_from_teacher_confirmation(
    client, custom_batch, format, repetitions
):
    rows = [(n, 6 - n, 3, 3, 3) for n in range(1, 6)] * repetitions
    base, batch = custom_batch(rows)
    listed = query(client, base, batch, limit=50).json()
    card = next(f for f in listed["items"] if f["type"] == "dimension_relation")
    detail = query(client, base, batch, card["discovery_id"] + "/query").json()
    listed["items"] = [card]
    saved = save(client, base, batch, listed).json()
    assert "教师确认发现" not in document_text(exported(client, base, batch, saved, format), format)
    original_snapshot = saved["analysis"]
    path = base + "/analysis-results/" + saved["result_id"]
    reviewed = client.patch(
        path,
        json={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": 1,
            "decisions": [{"candidate_id": card["discovery_id"], "status": "confirmed"}],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    saved = reviewed.json()
    assert saved["analysis"] == original_snapshot
    assert saved["discovery_findings"][0]["discovery"] == detail
    text = document_text(exported(client, base, batch, saved, format), format)
    assert "教师已确认" in text and "系统证据状态" in text
    assert all(note in text for note in detail["limitations"])
    if repetitions == 1:
        assert detail["status"] == "interpret_cautiously"
        assert detail["ranking"]["robustness"]["evaluated"] == 0
        assert "需谨慎解释" in text and "稳定性检查未完成" in text and "0/3" in text
    else:
        assert detail["status"] == "displayable"
        assert "按当前规则可展示" in text and "稳定性检查未完成" not in text
    dismissed = client.patch(
        path,
        json={
            "snapshot_id": batch["snapshot_id"],
            "expected_version": 2,
            "decisions": [{"candidate_id": card["discovery_id"], "status": "dismissed"}],
        },
    )
    assert dismissed.status_code == 200
    assert "教师确认发现" not in document_text(
        exported(client, base, batch, dismissed.json(), format), format
    )
