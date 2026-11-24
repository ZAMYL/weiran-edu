"""DS-04-S2 exercises the Qwen protocol locally with exactly two synthetic files."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO

import httpx
import pytest
from openpyxl import Workbook
from prototypes.adaptive_assessment.live import budget, pricing
from prototypes.durable_teacher_agent import live_admission

from app.ai.edu import material_qwen as live
from app.ai.edu.discovery.models import AgentError
from app.ai.edu.material_contract import Locator
from app.ai.edu.material_semantics import extract_material_input

BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@pytest.fixture
def files():
    book = Workbook()
    sheet = book.active
    sheet.title = "Private workbook sheet"
    sheet.append(["本轮阅卷说明"])
    sheet.append(["落袋计值", "凭签编号", "关卡索引"])
    sheet.append([8.75, "S-PRIVATE-1", "P-1"])
    sheet.append([7, "S-PRIVATE-2", "P-2"])
    stream = BytesIO()
    book.save(stream)
    book.close()

    csv = (
        "仅供复核\n"
        "机审裁言,关卡索引,入册号码,归并记号\n"
        "Accepted,P-1,S-PRIVATE-1,103\n"
        "Wrong Answer,P-2,S-PRIVATE-2,142\n"
    ).encode()
    xlsx = extract_material_input(stream.getvalue(), source_id="private-xlsx-version")
    plain = extract_material_input(csv, source_id="private-csv-version")
    materials = (xlsx, plain)
    labels = frozenset(
        Locator(material.source_id, 0, table.name, index, header)
        for material in materials
        for table in material.document.tables
        for index, header in enumerate(table.header)
    )
    return materials, labels


@pytest.fixture
def approval(tmp_path, monkeypatch, files):
    async def forbid_network(*_args, **_kwargs):
        pytest.fail("test attempted real network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_network)
    approval_path = tmp_path / "operator-approval.json"
    root = tmp_path / "ledger"
    monkeypatch.setenv(live.APPROVAL_ENV, str(approval_path))
    monkeypatch.setenv(live_admission.LEDGER_ROOT_ENV, str(root))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-placeholder")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", BASE)
    monkeypatch.setenv("WEIRAN_QWEN_MODEL", "qwen3.8-flash")

    now = datetime.now(UTC).date()
    price = replace(
        pricing.REVIEWED_PRICE_20261119,
        checked_on=(now - timedelta(days=1)).isoformat(),
        valid_until=(now + timedelta(days=1)).isoformat(),
    )
    resolve = pricing.reviewed_price

    def test_price(fingerprint):
        return price if fingerprint == price.fingerprint else resolve(fingerprint)

    monkeypatch.setattr(budget, "reviewed_price", test_price)
    monkeypatch.setattr(live_admission, "reviewed_price", test_price)
    code_sha = live_admission.code_fingerprint()
    inputs, labels = files
    scope = live.material_review_fingerprint(inputs, reviewed_header_labels=labels)

    def approve(**fields):
        document = {
            "campaign_id": "material-synthetic-01",
            "human_approval_reference": "synthetic-test-only",
            "independent_review_reference": "synthetic-test-only",
            "purpose": live.PURPOSE,
            "model": price.model,
            "endpoint_sha256": sha256(BASE.encode()).hexdigest(),
            "source_fingerprints": {live.PURPOSE: scope},
            "price_fingerprint": price.fingerprint,
            "valid_until": price.valid_until,
            "max_requests": 1,
            "max_output_tokens": 800,
            "total_token_limit": 992608,
            "cost_cap_cny": "1",
            "reviewed_code_sha256": code_sha,
            "ledger_root_sha256": live.material_ledger_fingerprint(root),
        }
        approval_path.write_text(json.dumps(document | fields), encoding="utf-8")

    return approve, root


def synthetic_transport(requests, *, after=None, usage=True):
    meanings = {
        "落袋计值": "score",
        "凭签编号": "student_key",
        "关卡索引": "problem_key",
        "机审裁言": "verdict",
        "入册号码": "student_key",
        "归并记号": "score",
    }

    def respond(request):
        wire = request.content.decode()
        for raw in (
            "S-PRIVATE-1",
            "Private workbook sheet",
            "private-xlsx-version",
            "private-csv-version",
            "Wrong Answer",
            "仅供复核",
            "本轮阅卷说明",
            "test-placeholder",
        ):
            assert raw not in wire
        body = json.loads(wire)
        assert body["model"] == "qwen3.8-flash"
        assert body["enable_thinking"] is False and body["max_tokens"] == 800
        overview = json.loads(body["messages"][1]["content"])["overview"]
        assert [doc["kind"] for doc in overview["files"]] == ["xlsx", "csv"]
        requests.append(overview)
        roles, fields, keys = [], [], []
        for table in overview["tables"]:
            roles.append({"table": table["ref"], "role": "programming", "evidence": [table["ref"]]})
            for col in table["columns"]:
                label = col["header_label"]
                ref, meaning = col["ref"], meanings[label]
                fields.append(
                    {
                        "column": ref,
                        "meaning": meaning,
                        "evidence": [ref],
                        "uncertain": label == "归并记号",
                    }
                )
                if meaning == "student_key":
                    keys.append(ref)
        links = [
            {
                "left": keys[0],
                "right": keys[1],
                "cardinality": "one_to_one",
                "evidence": keys,
            }
        ]
        response = {
            "model": "qwen3.8-flash",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"roles": roles, "fields": fields, "links": links}),
                    },
                }
            ],
        }
        if usage:
            response["usage"] = {"prompt_tokens": 250, "completion_tokens": 80, "total_tokens": 330}
        if after:
            after()
        return httpx.Response(200, json=response)

    return httpx.MockTransport(respond)


def invoke(files, transport, guard=lambda: None):
    inputs, labels = files
    return asyncio.run(
        live._understand(
            inputs, require_access=guard, reviewed_header_labels=labels, transport=transport
        )
    )


def test_two_unfamiliar_materials_use_one_budgeted_qwen_protocol_call(files, approval):
    approve, root = approval
    approve()
    requests = []
    transport = synthetic_transport(requests)
    plan = invoke(files, transport)
    assert len(requests) == 1
    assert [v.state for v in plan.assessment.roles.values()] == ["candidate", "candidate"]
    assert len(plan.assessment.fields) == 7
    assert plan.assessment.fields["F1.T1.C1", "score"].state == "candidate"
    assert plan.locators["F1.T1.C1"].column_name == "落袋计值"
    assert plan.assessment.fields["F2.T1.C4", "score"].state == "needs_confirmation"
    assert "F2.T1.C4" in plan.needs_confirmation
    assert plan.assessment.links["F1.T1.C2", "F2.T1.C3", "one_to_one"].state == "needs_confirmation"
    assert not any(cap.available for cap in plan.capabilities)
    assert all(
        "confirmed_field:code" in cap.missing
        for cap in plan.capabilities
        if cap.name == "code_error_patterns"
    )
    (journal,) = (root / "material-offline-test").glob("*.jsonl")
    last = json.loads(journal.read_text().splitlines()[-1])
    assert last["origin"] == "http_mock" and last["request_count"] == 1
    assert last["entries"][0]["actual_usage"]["total_tokens"] == 330
    assert not list(root.glob("*.jsonl"))
    with pytest.raises(AgentError, match="campaign_already_consumed"):
        invoke(files, transport)
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"purpose": "programming_synthetic_aggregate_remote_inference"}, "approval_invalid"),
        ({"source_fingerprints": {live.PURPOSE: "0" * 64}}, "packet_not_approved"),
        ({"ledger_root_sha256": "0" * 64}, "ledger_mismatch"),
        ({"cost_cap_cny": "0.01"}, "authorized_cost_limit"),
    ],
)
def test_unapproved_material_never_sends(files, approval, override, error):
    approve, _ = approval
    approve(**override)
    requests = []
    with pytest.raises(AgentError, match=error):
        invoke(files, synthetic_transport(requests))
    assert not requests


def test_public_live_entry_requires_its_own_approval(files, approval, monkeypatch):
    _, root = approval
    monkeypatch.delenv(live.APPROVAL_ENV)
    monkeypatch.setenv("WEIRAN_EDU_PROGRAMMING_LIVE_APPROVAL", "irrelevant-programming-approval")
    with pytest.raises(AgentError, match="live_admission_not_configured"):
        asyncio.run(live.understand_materials_with_qwen(files[0], require_access=lambda: None))
    assert not root.exists()


def test_revocation_after_response_bills_usage_without_publishing(files, approval):
    approve, root = approval
    approve()
    revoked = False

    def revoke():
        nonlocal revoked
        revoked = True

    def guard():
        if revoked:
            raise PermissionError("source_grant_revoked")

    requests = []
    with pytest.raises(PermissionError, match="source_grant_revoked"):
        invoke(files, synthetic_transport(requests, after=revoke), guard)
    (journal,) = (root / "material-offline-test").glob("*.jsonl")
    last = json.loads(journal.read_text().splitlines()[-1])
    assert len(requests) == last["request_count"] == 1
    assert last["entries"][0]["state"] == "settled"


def test_unknown_usage_preserves_reservation_and_rejects_plan(files, approval):
    approve, root = approval
    approve()
    requests = []
    with pytest.raises(AgentError, match="unverifiable_usage"):
        invoke(files, synthetic_transport(requests, usage=False))
    (journal,) = (root / "material-offline-test").glob("*.jsonl")
    entry = json.loads(journal.read_text().splitlines()[-1])["entries"][0]
    assert len(requests) == 1
    assert entry["state"] == "usage_unknown_reservation_held"
    assert entry["charged_cny_upper_bound"] == entry["reserved_cny"]
