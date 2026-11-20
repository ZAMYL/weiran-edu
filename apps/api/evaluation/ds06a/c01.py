"""Capability C: baseline + DS-03 + the real offline C-01 investigation session."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from prototypes.adaptive_assessment.data import Witness
from prototypes.adaptive_assessment.live.budget import CampaignBudget, OfflineProgrammingPermit
from prototypes.adaptive_assessment.live.demo import MOCK_DATE, native_response
from prototypes.adaptive_assessment.live.pricing import PRICE
from prototypes.research_agent_v2.programming import (
    INSPECT,
    OVERVIEW,
    investigate_programming,
)
from prototypes.research_agent_v2.programming_evidence import (
    EvidenceScope,
    EvidenceSnapshot,
    FeatureAssociation,
    ProgrammingSource,
    SubmissionEvidence,
    TestEvidence,
)

from app.ai.edu.discovery.models import AgentError, Limits

from .common import digest, evidence_ref, public_ref, rejection, result_shell
from .model import Case, snapshot_id
from .pipelines import build_ds03


class SnapshotProvider:
    """Read-only in-memory provider; the oracle is never visible to this object."""

    def __init__(self, material: EvidenceSnapshot):
        self.material = material

    def capture(self, scope: EvidenceScope) -> EvidenceSnapshot:
        if (scope.actor, scope.task, scope.batch, scope.snapshot) != (
            "teacher",
            self.material.snapshot,
            self.material.snapshot,
            self.material.snapshot,
        ):
            # The caller replaces task/batch with the snapshot to make this provider
            # independent of case labels and accidental scope leakage.
            raise AgentError("access_denied")
        return self.material

    def require_details(self, scope: EvidenceScope) -> None:
        self.capture(scope)


def run_c01(case: Case) -> dict:
    ds03, _ = build_ds03(case)
    if ds03["status"] == "rejected":
        return rejection(case.case_id, "C", ds03["error"])
    result = result_shell(case.case_id, "C")
    for key in (
        "statistics",
        "features",
        "feature_status",
        "candidates",
        "feature_cohorts",
        "root_assertions",
    ):
        result[key] = ds03[key]
    try:
        source = _source(case, ds03["feature_cohorts"])
        with TemporaryDirectory(prefix="ds06a-offline-") as directory:
            for problem in case.problems:
                _run_problem(case, source, problem, Path(directory), result)
    except (AgentError, ValueError) as error:
        return rejection(case.case_id, "C", str(error))
    result["source_verified"] = True
    return result


def _source(case: Case, cohorts: list[dict]) -> ProgrammingSource:
    snapshot = snapshot_id(case)
    rows, refs = [], {}
    for row in case.submissions:
        verdict = evidence_ref(row, "verdict")
        refs[verdict["reference_id"]] = verdict
        tests = []
        for test in row["tests"]:
            ref = evidence_ref(row, "test", point=test["point"])
            refs[ref["reference_id"]] = ref
            tests.append(TestEvidence(test["point"], test["status"], ref["reference_id"]))
        code_ref = None
        if row["code_state"] == "present" and row["code"] is not None:
            ref = evidence_ref(row, "code")
            refs[ref["reference_id"]] = ref
            code_ref = ref["reference_id"]
        rows.append(
            SubmissionEvidence(
                row["participant"],
                row["problem"],
                row["verdict"],
                verdict["reference_id"],
                code_ref,
                tuple(tests),
            )
        )
        fault = row.get("witness_fault")
        if fault == "verdict":
            damaged = dict(verdict)
            damaged["value"] = "accepted" if row["verdict"] != "accepted" else "wrong_answer"
            refs[verdict["reference_id"]] = damaged
        elif fault == "missing_verdict":
            refs.pop(verdict["reference_id"])
    features = []
    for cohort in cohorts:
        present = []
        for item in cohort["present"]:
            row = _row_at(case, cohort["problem"], item["participant"], item["location"])
            ref = evidence_ref(row, "feature", feature=cohort["feature"])
            refs[ref["reference_id"]] = ref
            present.append((item["participant"], ref["reference_id"]))
        features.append(
            FeatureAssociation(
                cohort["problem"],
                cohort["point"],
                cohort["status"],
                cohort["feature"],
                tuple(cohort["checked"]),
                tuple(present),
            )
        )
    material = EvidenceSnapshot(
        snapshot,
        case.problems,
        tuple(case.raw["participants"]),
        tuple(rows),
        tuple(Witness(**ref) for ref in refs.values()),
        tuple(features),
    )
    scope = EvidenceScope("teacher", snapshot, snapshot, snapshot)
    return ProgrammingSource(scope, SnapshotProvider(material))


def _row_at(case: Case, problem: str, participant: str, location: str) -> dict:
    return next(
        row
        for row in case.submissions
        if (row["problem"], row["participant"], row["location"]) == (problem, participant, location)
    )


def _run_problem(
    case: Case,
    source: ProgrammingSource,
    problem: str,
    directory: Path,
    result: dict,
    decide=None,
) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        facts = json.loads(payload["messages"][-1]["content"])["facts"]
        decision = (decide or _select_next_step)(facts)
        return httpx.Response(200, json=native_response(decision, len(requests)))

    campaign = "ds06a-" + digest(f"{case.case_id}:{problem}")[:20]
    permit = OfflineProgrammingPermit(source, campaign, PRICE.fingerprint)
    budget = CampaignBudget(permit, directory / campaign, origin="http_mock", today=MOCK_DATE)
    try:
        run, executor = asyncio.run(
            investigate_programming(
                source,
                (problem,),
                budget,
                transport=httpx.MockTransport(respond),
                limits=Limits(),
            )
        )
    finally:
        budget.close()
    if run.status != "completed":
        raise AgentError(run.reason)
    _collect_run(problem, run, executor, result)


def _select_next_step(facts: list[dict]) -> dict:
    """Bounded deterministic selector driven only by disclosed tool output."""

    if not facts:
        return {"name": OVERVIEW, "arguments": {}}
    overview = facts[0]
    inspected = len(facts) - 1
    directions = overview.get("directions", [])
    if inspected < len(directions) and len(facts) <= 4:
        direction = directions[inspected]
        return {
            "name": INSPECT,
            "arguments": {
                "overview_id": overview["fact_id"],
                "direction": direction["direction"],
            },
            "basis_fact_ids": [overview["fact_id"]],
        }
    selected = [fact["fact_id"] for fact in facts[1:] if fact.get("qualified")]
    return {
        "name": "finish",
        "arguments": {
            "selected_fact_ids": selected,
            "reason": "completed" if selected else "insufficient_data",
        },
    }


def _collect_run(problem: str, run, executor, result: dict) -> None:
    for fact in run.facts:
        if fact.tool != OVERVIEW:
            continue
        item = fact.view["items"][0]
        if item.get("state") == "available":
            result["overview_verdicts"][problem] = item["verdict_counts"]
    for raw_card in run.cards:
        card = {
            "problem": raw_card["problem"],
            "point": raw_card["test_point"],
            "status": raw_card["direction"]["phenomenon"],
            "features": raw_card["features"],
            "limitations": raw_card["limitations"],
            "other_directions_not_inspected": raw_card["other_directions_not_inspected"],
            "overview_limited": raw_card["overview_limited"],
            "evidence": {},
        }
        for query in raw_card["source_queries"]:
            witnesses = executor.resolve(query["fact_id"], query["metric_key"])
            card["evidence"][query["metric_key"]] = [
                public_ref(asdict(witness)) for witness in witnesses
            ]
        card["covered"] = _metric_evidence(card, "coverage")
        card["affected"] = _metric_evidence(card, "affected")
        result["phenomena"].append(
            {
                "problem": card["problem"],
                "point": card["point"],
                "status": card["status"],
                "covered": card["covered"],
                "affected": card["affected"],
            }
        )
        result["cards"].append(card)
        result["root_assertions"] += _root_assertions(raw_card)


def _metric_evidence(card: dict, suffix: str) -> list[dict]:
    return next(
        (refs for key, refs in card["evidence"].items() if key.endswith("." + suffix)),
        [],
    )


def _root_assertions(value) -> int:
    if isinstance(value, dict):
        count = 0
        for key, child in value.items():
            if key in {"root_cause", "caused_by", "confirmed_cause"} and child not in {
                None,
                "unconfirmed",
                "cause_unconfirmed",
            }:
                count += 1
            count += _root_assertions(child)
        return count
    if isinstance(value, (list, tuple)):
        return sum(_root_assertions(item) for item in value)
    return 0
