"""C-01 database replay through the real answer-sheet upload and SQLite reader."""

import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest
from prototypes.adaptive_assessment.live.budget import CampaignBudget, OfflineProgrammingPermit
from prototypes.adaptive_assessment.live.demo import MOCK_DATE, native_response
from prototypes.adaptive_assessment.live.pricing import PRICE
from prototypes.research_agent_v2.database_evidence import AssessmentEvidenceProvider
from prototypes.research_agent_v2.programming import (
    INSPECT,
    OVERVIEW,
    ProgrammingExecutor,
    investigate_programming,
)
from prototypes.research_agent_v2.programming_evidence import EvidenceScope, ProgrammingSource

from app.ai.edu.discovery.models import AgentError, Limits
from app.edu.assessments import AssessmentReader
from app.edu.originals import OriginalStore
from app.storage.database import Database
from edu.answersheet_samples import paper_html, simple_zip, zip_of

URL = "/api/v1/edu/assessments"
NAME = "Synthetic Personal Name"
PRIVATE_CODE = "values = [61302]; print(values[0])"
LABEL = "synthetic-private-problem"


def upload(client, *payloads: bytes) -> str:
    parts = [("file", (f"synthetic-{i}.zip", data)) for i, data in enumerate(payloads)]
    response = client.post(URL, files=parts)
    assert response.status_code == 201, response.text
    return response.json()["assessment_id"]


def binding(runtime, assessment: str) -> AssessmentEvidenceProvider:
    path = runtime.db.path
    reader = AssessmentReader(Database(path), originals=OriginalStore(path.parent / "originals"))
    overview = reader.overview(runtime.p, assessment)
    assert overview.source_snapshot_id is not None
    scope = EvidenceScope(
        runtime.p.teacher_id, "synthetic-task", assessment, overview.source_snapshot_id
    )
    return AssessmentEvidenceProvider(reader, runtime.p, scope)


def package(first: int, last: int) -> bytes:
    entries = {}
    for i in range(first, last + 1):
        verdict = "部分正确" if i <= 5 else "答案错误" if i <= 10 else "答案正确"
        point_status = "答案错误" if i <= 10 else "答案正确"
        html = paper_html(
            key=f"S{i:03}",
            name=f"{NAME} {i}",
            label=LABEL,
            verdict=verdict,
            rows=[("9", point_status, "0", "0.5 ms", "304 KB")],
            code=PRIVATE_CODE if i <= 5 else "print(sum([61302]))",
        ).replace("编译器: GXX", "编译器: Python")
        header = (
            "<thead><tr>"
            + "".join(
                f"<th>{label}</th>" for label in ("测试点", "结果", "测试点得分", "耗时", "内存")
            )
            + "</tr></thead>"
        )
        html = html.replace("<tbody>", header + "<tbody>")
        entries[f"包/正式答卷/restricted-S{i:03}.html"] = html
    return zip_of(entries)


def run_offline(source, directory):
    calls = []

    def respond(request):
        content = request.content.decode()
        assert all(
            secret not in content
            for secret in (
                NAME,
                PRIVATE_CODE,
                "61302",
                "restricted-S",
                "S001",
                LABEL,
                source.scope.actor,
                source.scope.batch,
                source.scope.task,
                source.scope.snapshot,
                *source.material.participants,
            )
        )
        facts = json.loads(json.loads(content)["messages"][-1]["content"])["facts"]
        calls.append(len(facts))
        if not facts:
            decision = {"name": OVERVIEW, "arguments": {}}
        elif len(facts) == 1:
            overview = facts[0]
            decision = {
                "name": INSPECT,
                "arguments": {
                    "overview_id": overview["fact_id"],
                    "direction": overview["directions"][0]["direction"],
                },
                "basis_fact_ids": [overview["fact_id"]],
            }
        else:
            decision = {
                "name": "finish",
                "arguments": {"selected_fact_ids": [facts[-1]["fact_id"]], "reason": "completed"},
            }
        return httpx.Response(200, json=native_response(decision, len(calls)))

    permit = OfflineProgrammingPermit(source, "test-db-programming", PRICE.fingerprint)
    budget = CampaignBudget(permit, directory, origin="http_mock", today=MOCK_DATE)
    try:
        result = asyncio.run(
            investigate_programming(
                source, source.material.problems, budget, transport=httpx.MockTransport(respond)
            )
        )
        assert budget.snapshot()["request_count"] == len(calls) == 3
        return result
    finally:
        budget.close()


def test_real_multizip_upload_investigation_and_restarted_witness_replay(client, runtime, tmp_path):
    left, right = package(1, 10), package(11, 20)
    assessment = upload(client, left, right)
    provider = binding(runtime, assessment)
    source = ProgrammingSource(provider.scope, provider)
    assert len(source.material.participants) == 20
    assert sum(item.verdict == "partial" for item in source.material.submissions) == 5
    assert (
        sum(
            test.point == "9" and test.status == "wrong_answer"
            for item in source.material.submissions
            for test in item.tests
        )
        == 10
    )
    run, executor = run_offline(source, tmp_path / "ledger")
    assert run.status == "completed" and run.model_requests == 3
    (card,) = run.cards
    assert card["direction"]["covered"] == 20
    assert card["direction"]["affected"] == 10
    assert {"feature": "indexing", "checked": 10, "present": 5} in card["features"]

    reopened = binding(runtime, assessment)
    fresh = ProgrammingSource(reopened.scope, reopened)
    assert fresh.material == source.material
    replay = ProgrammingExecutor(fresh, run.run_id, Limits(), fresh.material.problems)
    for entry in run.tools:
        replay.execute(entry.tool, replay.parse(entry.tool, entry.arguments))
    replay.verify_all()
    detail = run.selected_ids[0]
    assert replay.resolve(detail, "D1.coverage") == executor.resolve(detail, "D1.coverage")
    coverage = replay.resolve(detail, "D1.coverage")
    assert {item.source_sha256 for item in coverage} == {
        sha256(left).hexdigest(),
        sha256(right).hexdigest(),
    }
    (proof, *_) = replay.resolve(detail, "code.available")
    original = reopened.reader.original(
        runtime.p, assessment, proof.source_id, expected_sha256=proof.source_sha256
    )
    assert original.payload == left
    entry, _, anchor = proof.locator.partition("#")
    assert anchor.startswith("block/") and anchor.endswith("/code")
    with ZipFile(BytesIO(original.payload)) as archive:
        original_entry = archive.read(entry)
    assert PRIVATE_CODE.encode() in original_entry
    assert proof.value == PRIVATE_CODE
    assert proof.entry_sha256 == sha256(original_entry).hexdigest()


@pytest.mark.parametrize(
    "fault", ["tampered", "deleted", "snapshot", "asset_manifest", "code", "test"]
)
def test_capture_rejects_changed_zip_snapshots_and_database_witnesses(client, runtime, fault):
    payload = simple_zip()
    assessment = upload(client, payload)
    provider = binding(runtime, assessment)
    source = ProgrammingSource(provider.scope, provider)
    digest = sha256(payload).hexdigest()
    path = provider.reader.originals.path_for(digest)
    if fault == "tampered":
        path.write_bytes(payload + b"tampered")
    elif fault == "deleted":
        path.unlink()
    else:
        statements = {
            "snapshot": "UPDATE assessments SET source_snapshot_id=? WHERE assessment_id=?",
            "asset_manifest": (
                "UPDATE assessment_assets SET byte_size=byte_size+1 WHERE assessment_id=?"
            ),
            "code": "UPDATE submissions SET code_text='forged' WHERE assessment_id=?",
            "test": "UPDATE test_observations SET status='答案错误' WHERE submission_id IN "
            "(SELECT submission_id FROM submissions WHERE assessment_id=?)",
        }
        params = ("0" * 64, assessment) if fault == "snapshot" else (assessment,)
        with runtime.db.transaction(write=True) as connection:
            connection.execute(statements[fault], params)
    code = "programming_original_unavailable" if fault == "deleted" else "source_validation_failed"
    with pytest.raises(AgentError, match=code):
        source.validate()


def test_provider_rechecks_owner_task_and_teacher_credential(client, runtime):
    provider = binding(runtime, upload(client, simple_zip()))
    source = ProgrammingSource(provider.scope, provider)
    other = replace(provider.scope, actor=runtime.q.teacher_id)
    with pytest.raises(AgentError, match="access_denied"):
        AssessmentEvidenceProvider(provider.reader, runtime.q, other).capture(other)
    with pytest.raises(AgentError, match="access_denied"):
        provider.capture(replace(provider.scope, task="another-task"))
    runtime.admin.revoke_tokens(runtime.p.teacher_id)
    with pytest.raises(AgentError, match="access_denied"):
        source.validate()


def test_package_binding_cannot_be_swapped_in_same_assessment(client, runtime):
    assessment = upload(client, package(1, 1), package(2, 2))
    provider = binding(runtime, assessment)
    source = ProgrammingSource(provider.scope, provider)
    asset = source.material.witnesses[0].source_id
    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE submissions SET source_asset_id=? WHERE assessment_id=?", (asset, assessment)
        )
    with pytest.raises(AgentError, match="source_validation_failed"):
        source.validate()


def test_provider_refuses_ambiguous_package_locators_and_duplicate_submissions(client, runtime):
    assessment = upload(client, package(1, 1), package(2, 2))
    provider = binding(runtime, assessment)
    with runtime.db.transaction(write=True) as connection:
        connection.execute(
            "UPDATE submissions SET source_asset_id=NULL WHERE assessment_id=?", (assessment,)
        )
    with pytest.raises(AgentError, match="programming_ambiguous_source"):
        ProgrammingSource(provider.scope, provider)

    branch = "<div class='space-y-8'>"
    prefix, body = paper_html().split(branch, 1)
    block = branch + body.removesuffix("</body></html>")
    duplicate = zip_of({"包/正式答卷/repeated.html": prefix + block * 2 + "</body></html>"})
    provider = binding(runtime, upload(client, duplicate))
    with pytest.raises(AgentError, match="programming_duplicate_submission"):
        ProgrammingSource(provider.scope, provider)


def test_missing_source_unknown_status_and_missing_test_are_not_imputed(client, runtime):
    missing = paper_html(
        key="S001",
        verdict="答案错误",
        rows=[("1", "答案错误", "0", "2 ms", "32 KB"), ("2", "无法识别", "", "", "")],
        code="",
    ).replace("<pre class='box font-mono'></pre>", "")
    no_tests = paper_html(key="S002", verdict="编译错误", rows=[], code="无")
    unanswered = paper_html(key="S003", verdict="未作答", rows=[], code="无")
    unknown_language = paper_html(
        key="S004", verdict="答案错误", rows=[("1", "答案错误", "0", "2 ms", "32 KB")]
    ).replace("编译器: GXX", "编译器: GCC")
    asset = zip_of(
        {
            f"包/正式答卷/S{i:03}.html": doc
            for i, doc in enumerate([missing, no_tests, unanswered, unknown_language], 1)
        }
    )
    provider = binding(runtime, upload(client, asset))
    source = ProgrammingSource(provider.scope, provider)
    first, second, third, fourth = source.material.submissions
    assert first.code_ref is None and first.verdict == "wrong_answer"
    assert [(test.point, test.status) for test in first.tests] == [
        ("1", "wrong_answer"),
        ("2", "unknown"),
    ]
    assert second.verdict == "compile_error" and second.tests == () and second.code_ref is None
    assert third.verdict == "not_attempted" and third.tests == () and third.code_ref is None
    assert fourth.code_ref is not None and source.material.features == ()


def test_revoked_original_blocks_old_result_before_final_return(
    client, runtime, tmp_path, monkeypatch
):
    provider = binding(runtime, upload(client, package(1, 20)))
    source = ProgrammingSource(provider.scope, provider)
    finish = ProgrammingExecutor.finish

    def revoke_after_finish(executor, args):
        cards = finish(executor, args)
        assert cards
        with runtime.db.transaction(write=True) as connection:
            connection.execute(
                "UPDATE assessment_assets SET storage_state='revoked' WHERE assessment_id=?",
                (provider.scope.batch,),
            )
        return cards

    monkeypatch.setattr(ProgrammingExecutor, "finish", revoke_after_finish)
    with pytest.raises(AgentError, match="programming_original_unavailable"):
        run_offline(source, tmp_path / "revoked")
    with pytest.raises(AgentError, match="programming_original_unavailable"):
        provider.require_details(source.scope)
