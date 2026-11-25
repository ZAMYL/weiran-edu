"""The three DS-04-S1 cases start at actual in-memory input files."""

import asyncio
import json
from hashlib import sha256
from io import BytesIO

from openpyxl import Workbook

from app.ai.edu.material_contract import Locator, MaterialInput
from app.ai.edu.material_semantics import extract_material_input, understand_materials
from app.importing.edu.detect import detect_asset
from app.importing.edu.extract import extract_document


def test_unfamiliar_workbook_with_reordered_columns():
    book = Workbook()
    grades = book.active
    grades.title = "本轮计分明细"
    grades.append(["成绩", "题号", "考号"])
    grades.append([8.25, "P-02", "S001"])
    grades.append([6.5, "P-02", "S002"])

    reviews = book.create_sheet("评阅员复核")
    reviews.append(["评语", "考号", "维度评分"])
    reviews.append(["NEVER_SEND_SYNTHETIC_FEEDBACK", "S001", 4.25])
    reviews.append(["合成评语", "S002", 3.5])
    memory = BytesIO()
    book.save(memory)
    book.close()
    data = memory.getvalue()

    material = extract_material_input(data, source_id="asset-xlsx:v1", content_type="text/csv")
    assert material.document.kind == "xlsx" and material.coverage == "unknown"
    assert len(material.document.tables) == 2
    assert material.document.tables[0].rows[0][0] == "8.25"
    plan = asyncio.run(understand_materials((material,)))

    assert plan.assessment.roles["F1.T1", "programming"].state == "candidate"
    assert plan.assessment.roles["F1.T2", "evaluation"].state == "candidate"
    assert plan.assessment.fields["F1.T1.C1", "score"].state == "candidate"
    assert plan.assessment.fields["F1.T2.C3", "dimension_score"].state == "candidate"
    assert plan.locators["F1.T1.C1"] == Locator("asset-xlsx:v1", 0, "本轮计分明细", 0, "成绩")
    assert plan.locators["F1.T2.C3"] == Locator("asset-xlsx:v1", 1, "评阅员复核", 2, "维度评分")
    assert {"F1.T1.C1", "F1.T2.C3"} <= set(plan.needs_confirmation)
    assert not plan.assessment.links  # Two sheets are still one source.
    assert not any(cap.available for cap in plan.capabilities)


def test_unfamiliar_csv_with_scores_verdicts_and_no_source_code():
    data = (
        "OJ Verdict,实得分,小题代码,参赛编号\n"
        "Accepted,7.75,Q-2,contest-001\n"
        "Wrong Answer,3,Q-2,contest-002\n"
        "Timeout,1.5,Q-9,contest-002\n"
    ).encode()
    # B-02 already holding extracted structures can pass them to the same planner.
    document = extract_document(detect_asset(data).kind, data)
    material = MaterialInput(document, source_id="asset-csv:v7")
    assert document.kind == "csv" and len(document.tables[0].rows) == 3

    plan = asyncio.run(understand_materials((material,)))
    assert plan.assessment.roles["F1.T1", "programming"].state == "candidate"
    assert {meaning for _, meaning in plan.assessment.fields} == {
        "student_key",
        "problem_key",
        "score",
        "verdict",
    }
    assert all(verdict.state == "candidate" for verdict in plan.assessment.fields.values())
    assert plan.locators["F1.T1.C4"] == Locator("asset-csv:v7", 0, "csv", 3, "参赛编号")
    assert plan.assessment.fields["F1.T1.C2", "score"].evidence == ("F1.T1.C2",)
    features = next(cap for cap in plan.capabilities if cap.name == "code_error_patterns")
    assert not features.available and "confirmed_field:code" in features.missing
    assert not any(cap.available for cap in plan.capabilities)


def test_ambiguous_and_incomplete_fields_local_validation():
    left = (
        "X档,Y额,Z栏\nlocal-01,10,DO_NOT_SEND_RAW_VALUE\n,,only-observation\nlocal-03,8,other\n"
    ).encode()
    right = "甲列,乙列\nlocal-01,qualifier\nlocal-03,other\n".encode()
    inputs = tuple(
        extract_material_input(data, source_id=sha256(data).hexdigest()) for data in (left, right)
    )
    local = asyncio.run(understand_materials(inputs))
    assert not local.assessment.fields
    assert {"F1.T1", "F1.T1.C2", "F1.T1.C3", "F2.T1.C1"} <= set(local.needs_confirmation)

    async def synthetic_sender(payload):
        wire = json.dumps(payload, ensure_ascii=False)
        assert all(
            private not in wire
            for private in (
                "local-01",
                "DO_NOT_SEND_RAW_VALUE",
                "Y额",
                "X档",
                inputs[0].source_id,
                inputs[1].source_id,
            )
        )
        overview = json.loads(payload["messages"][1]["content"])["overview"]
        columns = overview["tables"][0]["columns"]
        assert columns[1]["missing"] == 1
        assert columns[1]["header_tokens"] == []
        left_ref, score_ref, text_ref = (col["ref"] for col in columns)
        right_ref = overview["tables"][1]["columns"][0]["ref"]
        return {
            "content": json.dumps(
                {
                    "roles": [],
                    "fields": [
                        {"column": ref, "meaning": meaning, "evidence": [ref]}
                        for ref, meaning in (
                            (left_ref, "student_key"),
                            (right_ref, "student_key"),
                            (score_ref, "score"),
                            (text_ref, "score"),
                        )
                    ],
                    "links": [
                        {
                            "left": left_ref,
                            "right": right_ref,
                            "cardinality": "one_to_one",
                            "evidence": [left_ref, right_ref],
                        }
                    ],
                }
            )
        }

    plan = asyncio.run(understand_materials(inputs, send=synthetic_sender))
    score = plan.assessment.fields["F1.T1.C2", "score"]
    assert score.state == "needs_confirmation"
    assert score.reasons == ("ambiguous_field_semantics",)
    assert plan.assessment.fields["F1.T1.C3", "score"].state == "unsupported"
    link = plan.assessment.links["F1.T1.C1", "F2.T1.C1", "one_to_one"]
    assert link.state == "needs_confirmation"
    assert {"identity_namespace_unproved", "missing_join_keys"} <= set(link.reasons)
    assert {plan.locators[ref].source_id for ref in link.evidence} == {
        inputs[0].source_id,
        inputs[1].source_id,
    }
    assert {"F1.T1.C2", "F1.T1.C1", "F2.T1.C1"} <= set(plan.needs_confirmation)
    assert not any(cap.available for cap in plan.capabilities)
