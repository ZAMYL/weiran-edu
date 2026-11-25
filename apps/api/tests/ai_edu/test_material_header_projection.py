"""Review exactly which unfamiliar field labels the DS-04 sender can see."""

import asyncio
import json
from io import BytesIO

from openpyxl import Workbook

from app.ai.edu.material_contract import Locator, MaterialInput
from app.ai.edu.material_semantics import extract_material_input, understand_materials


def _material(*sheets: tuple) -> MaterialInput:
    book = Workbook()
    book.remove(book.active)
    for name, headers, rows in sheets:
        sheet = book.create_sheet(name)
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return extract_material_input(stream.getvalue(), source_id="private-source-version")


def _reviewed(material, labels) -> frozenset[Locator]:
    return frozenset(
        Locator(material.source_id, ti, table.name, ci, label)
        for ti, table in enumerate(material.document.tables)
        for ci, label in enumerate(table.header)
        if label in labels
    )


def _synthetic_sender(semantics, requests, *, uncertain=()):
    async def send(payload):
        requests.append(payload)
        view = json.loads(payload["messages"][1]["content"])["overview"]
        fields = [
            {
                "column": col["ref"],
                "meaning": semantics[label],
                "evidence": [col["ref"]],
                "uncertain": label in uncertain,
            }
            for table in view["tables"]
            for col in table["columns"]
            if (label := col.get("header_label")) in semantics
        ]
        return {"content": json.dumps({"roles": [], "fields": fields, "links": []})}

    return send


def test_new_chinese_labels_are_useful_only_after_review():
    semantics = {
        "卷内符记": "student_key",
        "赛项序牌": "problem_key",
        "结算所得": "score",
    }
    material = _material(
        ("private-sheet-a", list(semantics), [("synthetic-42", "p-1", 8.25)]),
    )
    assert not asyncio.run(understand_materials((material,))).assessment.fields

    requests = []
    sender = _synthetic_sender(semantics, requests)
    without_review = asyncio.run(understand_materials((material,), send=sender))
    assert not without_review.assessment.fields
    assert all(label not in json.dumps(requests[0], ensure_ascii=False) for label in semantics)

    plan = asyncio.run(
        understand_materials(
            (material,), send=sender, reviewed_header_labels=_reviewed(material, semantics)
        )
    )
    assert {
        (plan.locators[ref].column_name, meaning, verdict.state)
        for (ref, meaning), verdict in plan.assessment.fields.items()
    } == {(label, meaning, "candidate") for label, meaning in semantics.items()}
    wire = json.dumps(requests[1], ensure_ascii=False)
    assert all(label in wire for label in semantics)
    assert all(x not in wire for x in ("synthetic-42", "private-sheet-a", material.source_id))
    assert all(label not in json.dumps(plan.overview, ensure_ascii=False) for label in semantics)
    assert not any(item.available for item in plan.capabilities)


def test_reordered_sheets_bind_review_to_each_exact_column():
    semantics = {"卷内符记": "student_key", "结算所得": "score", "判卷短语": "feedback"}
    material = _material(
        ("private-sheet-b", ["结算所得", "卷内符记"], [(7, "S-07")]),
        ("private-sheet-c", ["卷内符记", "判卷短语", "结算所得"], [("S-07", "raw-feedback", 6)]),
    )
    reviewed = _reviewed(material, semantics) - {
        # A review at an old position must not release a moved label.
        Locator(material.source_id, 1, "private-sheet-c", 2, "结算所得")
    } | {Locator(material.source_id, 1, "private-sheet-c", 0, "结算所得")}
    requests = []
    plan = asyncio.run(
        understand_materials(
            (material,),
            send=_synthetic_sender(semantics, requests),
            reviewed_header_labels=reviewed,
        )
    )
    assert {
        (plan.locators[ref].table_index, plan.locators[ref].column_index, meaning)
        for (ref, meaning), verdict in plan.assessment.fields.items()
        if verdict.state == "candidate"
    } == {
        (0, 0, "score"),
        (0, 1, "student_key"),
        (1, 0, "student_key"),
        (1, 1, "feedback"),
    }
    assert ("F1.T2.C3", "score") not in plan.assessment.fields
    assert "F1.T2.C3" in plan.needs_confirmation
    assert not plan.assessment.links
    assert "raw-feedback" not in json.dumps(requests, ensure_ascii=False)


def test_ambiguous_labels_and_sensitive_headers_stay_unconfirmed():
    headers = [
        "记录片段",
        "小计量",
        "合成姓名甲",
        "电话 13800138000",
        "contact@example.invalid",
        "未获审阅的栏",
    ]
    material = _material(
        (
            "private-sheet-d",
            headers,
            [("合成姓名甲", "invalid-numeric", "participant-42", "raw-a", "raw-b", "raw-c")],
        ),
    )
    requests = []
    plan = asyncio.run(
        understand_materials(
            (material,),
            send=_synthetic_sender(
                {"记录片段": "student_key", "小计量": "score"},
                requests,
                uncertain={"记录片段"},
            ),
            reviewed_header_labels=_reviewed(material, headers[:-1]),
        )
    )
    wire = json.dumps(requests[0], ensure_ascii=False)
    assert all(label not in wire for label in headers[2:])
    assert all(value not in wire for value in ("participant-42", "invalid-numeric", "raw-c"))
    assert plan.assessment.fields["F1.T1.C1", "student_key"].state == "needs_confirmation"
    assert plan.assessment.fields["F1.T1.C2", "score"].state == "unsupported"
    assert {ref for ref, _ in plan.assessment.fields} == {"F1.T1.C1", "F1.T1.C2"}
    assert {"F1.T1.C1", "F1.T1.C3", "F1.T1.C6"} <= set(plan.needs_confirmation)
    assert not any(item.available for item in plan.capabilities)
