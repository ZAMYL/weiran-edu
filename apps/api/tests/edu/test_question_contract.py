"""Lin Ming's item contract, separate from the compatible old five-dimension wire."""

from fractions import Fraction

from .test_question_xlsx import synthetic


def test_actual_items_scales_distribution_reread_and_batch_list(client):
    def change(w):
        w["Sheet2"]["B2"] = None
        w["Sheet2"]["B3"] = -1
        w["Sheet2"]["B4"] = 5.5
        w["Sheet2"]["C2"] = 1.001

    response = client.post(
        "/api/v1/edu/imports/preview",
        files={"file": ("synthetic.xlsx", synthetic(change), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    view = response.json()
    assert view["available_analyses"] == ["item_statistics"]
    item = view["recognized_items"][0]
    assert item["display_name"] == "原始题目1" and item["kind"] == "question"
    assert item["scale"] == {"minimum": "0", "maximum": "10", "step": "0.01", "unit": "分"}
    assert view["quality_counts"] == {"missing": 1, "out_of_range": 1, "off_step": 1, "valid": 257}
    ref = client.post(
        f"/api/v1/edu/imports/{view['import_id']}/commit",
        json={"preview_version": view["preview_version"], "acknowledge_warnings": True},
    ).json()
    path = f"/api/v1/edu/batches/{ref['batch_id']}"
    reread = client.get(path).json()
    assert reread["recognized_items"] == view["recognized_items"]
    assert client.get("/api/v1/edu/batches").json()["total_count"] == 0
    listed = client.get("/api/v1/edu/batches?include_dynamic=true").json()
    assert listed["total_count"] == 1 and listed["items"][0]["batch_id"] == ref["batch_id"]
    assert listed["items"][0]["recognized_items"] == view["recognized_items"]
    result = client.get(
        path + "/statistics", params={"snapshot_id": ref["snapshot_id"], "item_id": "Q01"}
    )
    assert result.status_code == 200, result.text
    stats = result.json()["item_statistics"]
    assert len(stats) == 1 and stats[0]["item_id"] == "Q01"
    first = stats[0]
    assert (
        first["valid_count"],
        first["missing_count"],
        first["invalid_count"],
        first["denominator"],
    ) == (18, 1, 1, 20)
    assert Fraction(**first["mean"]["exact"]) == Fraction(209, 72)
    assert [
        (Fraction(**d["exact"]), d["count"], d["denominator"]) for d in first["distribution"]
    ] == [(Fraction(11, 4), 17, 20), (Fraction(11, 2), 1, 20)]
    assert sum(d["count"] for d in first["distribution"]) == 18
    assert client.get(path + "/statistics", params={"item_id": "not-a-question"}).status_code == 404
