"""HTTP wiring against #98's offline database seam: submit -> facts -> scores -> review -> export.

No network, no API key, no paid request. The task grant is deliberately marked
``database_live`` so the HTTP submit exercises the Live branch, while the injected
runner uses ``run_database_profiles_offline`` and persists explicit Mock provenance.
"""

import asyncio
import json
from io import BytesIO

import httpx
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from prototypes.adaptive_assessment.live.budget import OfflineSnapshotPermit
from prototypes.adaptive_assessment.live.demo import native_response
from prototypes.adaptive_assessment.live.pricing import RESEARCH_PRICE_20261104
from prototypes.capability_profiles.agent import ProfileMockPolicy, project_selected
from prototypes.durable_teacher_agent.models import ProfileInvestigationResult
from prototypes.durable_teacher_agent.profiles import extend_proof, projection, uncovered
from prototypes.durable_teacher_agent.service import DurableBudget, Invocation, proof
from prototypes.qwen_execution import DatabaseTaskInput, ExecutionProvenance
from prototypes.qwen_execution.testing import run_database_profiles_offline
from prototypes.research_agent_v2.goals import Goal
from prototypes.teacher_agent_bridge.models import InvestigationResult, InvocationBudget, ToolStep
from prototypes.teacher_agent_bridge.provider import ALIASES
from pydantic import SecretStr

from app.ai.edu.discovery.models import AgentError
from app.ai.edu.qwen_config import QwenSettings

from .test_database_profiles import profile_host
from .test_tasks import actor, upload


def transport_for(captured):
    policy = ProfileMockPolicy()

    def respond(request):
        payload = json.loads(request.content)
        captured.append(payload)
        view = json.loads(payload["messages"][-1]["content"])
        return httpx.Response(200, json=native_response(policy.choose(view), len(captured)))

    return httpx.MockTransport(respond)


def _persist_offline_result(service, task_id, row, actor, source, raw, book, executor):
    if raw["status"] != "completed":
        raise AgentError("offline_profile_not_completed")
    raw["goal"] = Goal(kind="priority", items=()).view()
    raw["missing_evidence"] = []
    raw["trace"] = [
        {
            "tool": event.get("tool"),
            "round": event["round"],
            "phase": event["phase"],
            "status": event["status"],
            "fact_ids": event.get("fact_ids", []),
        }
        for event in raw["trace"]
    ]
    raw["investigation_coverage"] = {"uncovered_direction_ids": uncovered(book, executor)}
    raw["stop_reason"] = raw["profile_stop"] if book.candidates else "no_supported_findings"
    # Re-run the projection with a fresh staged list. The original session keeps
    # its staged partitions private; replay needs the same cumulative disclosure.
    selection_origin = (
        raw["profiles"][0].get("selection_origin", "http_mock")
        if raw["profiles"]
        else "http_mock"
    )
    staged = []
    raw["profiles"] = project_selected(
        book, raw, executor, staged=staged, selection_origin=selection_origin
    )
    projected = projection(source, raw, book, executor)
    facts = Invocation(service, row).verified_facts(
        source, executor, row["batch_id"], row["snapshot_id"], task_id
    )
    records_path = (
        f"/api/v1/edu/batches/{row['batch_id']}/ai-investigation-tasks/"
        f"{task_id}/records/query"
    )
    facts = tuple(
        fact.model_copy(
            update={
                "source_refs": tuple(
                    reference.model_copy(update={"records_path": records_path})
                    for reference in fact.source_refs
                )
            }
        )
        for fact in facts
    )
    result = ProfileInvestigationResult(
        run_id=task_id,
        batch_id=row["batch_id"],
        snapshot_id=row["snapshot_id"],
        source_manifest_sha256=source.fingerprint,
        batch_record_count=len(source.envelope.data.participants),
        strategy="adaptive",
        status=raw["status"],
        stop_reason=raw["stop_reason"],
        controlled_goal=raw["goal"]["type"],
        goal_items=tuple(ALIASES[item] for item in raw["goal"]["items"]),
        missing_evidence=(),
        facts=facts,
        selected_fact_ids=tuple(card["fact_id"] for card in raw["findings"]),
        retained_fact_ids=tuple(raw.get("retained_fact_ids", ())),
        trace=tuple(
            ToolStep(
                tool=event["tool"],
                round=event["round"],
                phase=event["phase"],
                status=event["status"],
                fact_ids=tuple(event["fact_ids"]),
            )
            for event in raw["trace"]
        ),
        budget=InvocationBudget(
            model_requests=raw["model_requests"],
            tool_attempts=raw["tool_attempts"],
            verification_replays=executor.replay_executions,
        ),
        uncovered_direction_count=len(raw["investigation_coverage"]["uncovered_direction_ids"]),
        report_integration="eligible_facts_require_formal_recomputation",
        profile_projection=projected,
    )
    verified = extend_proof(proof(executor), result, executor, staged, service.profile_limits)
    service.store.finish(task_id, actor, result, verified)
    service.store.provenance(
        task_id,
        ExecutionProvenance(
            origin="http_mock",
            state="completed",
            request_state="responses_received"
            if raw["model_requests"]
            else "not_run",
            model_requests=min(raw["model_requests"], 6),
            tool_attempts=min(raw["tool_attempts"], 5),
            source_replay_verified=True,
        ).model_dump(mode="json"),
    )


def offline_runner(service, task_id, row, actor, transport, scopes):
    source = service.source(row, actor, running=True)
    permit = OfflineSnapshotPermit(
        source, row["campaign_id"], RESEARCH_PRICE_20261104.fingerprint
    )
    budget = DurableBudget(permit, service.directory / "offline-ledger", service.store, task_id)
    try:
        binding = DatabaseTaskInput(
            task_id=task_id,
            batch_id=row["batch_id"],
            snapshot_id=row["snapshot_id"],
            actor=actor,
            store=service.store,
            source=source,
            budget=budget,
            settings=QwenSettings(
                SecretStr("offline-synthetic-only"),
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            require_externalization=lambda scope: scopes.append(scope),
        )
        raw, book, executor = asyncio.run(
            run_database_profiles_offline(
                binding, limits=service.profile_limits, transport=transport
            )
        )
        _persist_offline_result(service, task_id, row, actor, source, raw, book, executor)
    finally:
        budget.close()


def test_live_task_without_approval_fails_closed_with_a_live_code(tmp_path, monkeypatch):
    monkeypatch.delenv("WEIRAN_EDU_QWEN_LIVE_APPROVAL", raising=False)
    fixture, service = profile_host(tmp_path)
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture, "balanced72")
        principal = actor(fixture, service)
        grant_id = service.store.grant(principal, batch["batch_id"], batch["snapshot_id"])
        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_grants SET purpose='database_live' WHERE grant_id=?",
                (grant_id,),
            )
        base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
        response = client.post(
            base,
            json={
                "snapshot_id": batch["snapshot_id"],
                "question": "有哪些值得关注的问题？",
                "idempotency_key": "offline-live-refusal-01",
            },
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        response = client.get(
            f"{base}/{task_id}", params={"snapshot_id": batch["snapshot_id"]}
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["state"] == "failed"
        assert view["error_code"] == "AI_LIVE_ADMISSION_REQUIRED"
        assert view["result"] is None


def test_offline_live_wiring_submit_facts_scores_review_export(tmp_path):
    fixture, service = profile_host(tmp_path)
    captured, scopes = [], []
    service.live_runner = lambda svc, task_id, row, actor: offline_runner(
        svc, task_id, row, actor, transport_for(captured), scopes
    )
    with TestClient(fixture.app, headers={"Authorization": "Bearer " + fixture.token}) as client:
        batch = upload(client, fixture, "balanced72")
        principal = actor(fixture, service)
        grant_id = service.store.grant(
            principal, batch["batch_id"], batch["snapshot_id"]
        )
        with service.store.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_synthetic_grants SET purpose='database_live' WHERE grant_id=?",
                (grant_id,),
            )
        base = f"/api/v1/edu/batches/{batch['batch_id']}/ai-investigation-tasks"
        body = {
            "snapshot_id": batch["snapshot_id"],
            "question": "有哪些值得关注的问题？",
            "idempotency_key": "offline-live-wiring-01",
        }
        response = client.post(base, json=body)
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        response = client.get(
            f"{base}/{task_id}", params={"snapshot_id": batch["snapshot_id"]}
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["state"] == "completed", view
        assert view["execution_origin"] == "http_mock"
        assert view["real_qwen"] == "not_run"
        assert view["provenance"]["origin"] == "http_mock"
        assert view["provenance"]["source_replay_verified"] is True
        assert captured and scopes
        assert view["model_requests"] == len(captured)
        facts = view["result"]["facts"]
        assert facts
        assert all(fact["source_verified"] for fact in facts)

        group = next(fact for fact in facts if fact["method"] == "compare_student_groups")
        source = next(ref for ref in group["source_refs"] if ref["metric_key"] == "target.valid")
        response = client.post(
            source["records_path"], json=source["records_query"] | {"limit": 2}
        )
        assert response.status_code == 200, response.text
        records = response.json()
        assert records["total_count"] > 0 and len(records["items"]) <= 2

        profile = view["result"]["profile_projection"]["profiles"][0]
        dimension = profile["dimensions"][0]["dimension_code"]
        response = client.post(
            f"{base}/{task_id}/profiles/scores/query",
            json={
                "snapshot_id": batch["snapshot_id"],
                "profile_id": profile["profile_id"],
                "dimension_code": dimension,
                "side": "target",
                "offset": 0,
                "limit": 10,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["detail_status"] in {"available", "privacy_limited"}

        submission = {
            "snapshot_id": batch["snapshot_id"],
            "fact_ids": [group["fact_id"]],
        }
        response = client.post(f"{base}/{task_id}/review-submissions", json=submission)
        assert response.status_code == 200, response.text
        receipt = response.json()
        response = client.patch(
            receipt["review_path"],
            json={
                "snapshot_id": batch["snapshot_id"],
                "expected_version": 1,
                "decisions": [
                    {"candidate_id": item, "status": "confirmed"}
                    for item in receipt["candidate_ids"]
                ],
            },
        )
        assert response.status_code == 200, response.text
        for fmt in ("md", "xlsx"):
            exported = client.get(
                receipt["export_path"],
                params={
                    "snapshot_id": batch["snapshot_id"],
                    "expected_version": 2,
                    "format": fmt,
                },
            )
            assert exported.status_code == 200, exported.text[:200]
            if fmt == "xlsx":
                workbook = load_workbook(BytesIO(exported.content), data_only=False)
                assert len(workbook.sheetnames) >= 4
                workbook.close()
            else:
                assert exported.text
