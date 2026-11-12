"""No Fact promotion: match recomputed formal discoveries, then use original results.

Relation/group facts need an exactly matching qualified formal discovery. Observations
cannot be approximated by another quantile/condition or silently relabelled.
"""

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from fractions import Fraction

from app.analysis.edu.discovery.models import VERSION as FORMAL_VERSION
from app.analysis.edu.discovery.models import Discovery, DiscoveryResult
from app.analysis.edu.models import Dimension, RecordRef
from app.edu.analysis import DIMENSION_MAP, OPERATOR_MAP
from app.edu.auth import Principal
from app.edu.discoveries import DiscoveryReader
from app.edu.discovery_bridge import DiscoveryInput
from app.edu.discovery_disconnect import CancelCheck, cancellation, check_cancelled
from app.edu.discovery_models import DiscoverySpec
from app.edu.discovery_results import DiscoveryResultService
from app.edu.review_models import AnalysisCreate, AnalysisSnapshot, DiscoverySelection
from fastapi import Request
from starlette.concurrency import run_in_threadpool

from prototypes.adaptive_assessment.agent import AdaptiveFact
from prototypes.adaptive_assessment.analysis import VERSION as AGENT_VERSION
from prototypes.teacher_agent_bridge.provider import ALIASES

from .models import ReviewReceipt, ReviewSubmission
from .service import TaskService
from .store import TaskStore, blocked


class ReviewDatabase:
    """Wrap the ORIGINAL result-save transaction, not a new report database.

    AI purpose checks and the task/result link commit atomically with the existing
    pending result. No partial save can survive a revocation or linking failure.
    """

    def __init__(
        self,
        store: TaskStore,
        row: dict,
        actor: Principal,
        ids: dict,
        cancelled: CancelCheck | None,
    ):
        self.store, self.row, self.actor, self.ids = store, row, actor, ids
        self.cancelled = cancelled
        self.path = store.database.path

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with self.store.database.transaction(write=write) as c:
            if write:
                current = self.store._row(c, self.row["task_id"])
                self.store.check(c, current, self.actor)
                if current["state"] != "completed" or current["review_json"] is not None:
                    blocked("AI_REVIEW_CONFLICT")
                check_cancelled(self.cancelled)
                before = c.execute(
                    "SELECT result_id FROM analysis_results ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                self.store.event(
                    c, self.row["task_id"], "review_requested", {"fact_ids": list(self.ids)}
                )
            yield c
            if write:
                self.store.check(c, self.store._row(c, self.row["task_id"]), self.actor)
                after = c.execute(
                    "SELECT result_id FROM analysis_results ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                if after is None or before is not None and before[0] == after[0]:
                    blocked("AI_REVIEW_NOT_SAVED")
                base = f"/api/v1/edu/batches/{self.row['batch_id']}/analysis-results/{after[0]}"
                receipt = ReviewReceipt(
                    task_id=self.row["task_id"],
                    batch_id=self.row["batch_id"],
                    snapshot_id=self.row["snapshot_id"],
                    result_id=after[0],
                    version=1,
                    candidate_ids=tuple(self.ids.values()),
                    fact_candidate_map=self.ids,
                    agent_rule_version=AGENT_VERSION,
                    formal_rule_version=FORMAL_VERSION,
                    result_path=base,
                    review_path=base,
                    export_path=base + "/export",
                )
                c.execute(
                    "UPDATE ai_synthetic_tasks SET review_json=? WHERE task_id=?",
                    (receipt.model_dump_json(), self.row["task_id"]),
                )
                self.store.event(
                    c, self.row["task_id"], "pending_review_saved", receipt.model_dump(mode="json")
                )
                check_cancelled(self.cancelled)


def score_pairs(records: Sequence[RecordRef], dimension: Dimension) -> set[tuple[str, str]]:
    return {
        (r.student_id, e.location)
        for r in records
        for e in r.evidence_refs
        if e.dimension == dimension
    }


def match_fact(fact: AdaptiveFact, data: DiscoveryInput, result: DiscoveryResult) -> Discovery:
    actual = fact.calculation.values
    actual_sources = {
        s.key: set(zip(s.participants, s.references, strict=True)) for s in fact.item_sources
    }
    if fact.tool == "query_dimension_relation":
        pair = {DIMENSION_MAP[ALIASES[fact.query["arguments"][i]]] for i in ("left", "right")}
        matches = [
            f
            for f in result.findings
            if f.type == "dimension_relation" and {f.payload.left, f.payload.right} == pair
        ]
        if len(matches) != 1:
            blocked("AI_FORMAL_DISCOVERY_NOT_QUALIFIED", 422)
        finding = matches[0]
        formal = finding.payload
        values_ok = (
            formal.coefficient == actual["rho"]
            and formal.paired_count == actual["paired_count"]
            and len(formal.excluded_records) == actual["excluded_count"]
        )
        records = tuple(o.reference for o in formal.observations)
        expected = {
            side + ".paired": score_pairs(
                records, DIMENSION_MAP[ALIASES[fact.query["arguments"][side]]]
            )
            for side in ("left", "right")
        }
    elif fact.tool == "compare_student_groups":
        args = fact.query["arguments"]
        conditions = {
            (
                DIMENSION_MAP[ALIASES[p["item_id"]]],
                OPERATOR_MAP[
                    {"<": "lt", "<=": "lte", "==": "eq", ">=": "gte", ">": "gt"}[p["operator"]]
                ],
                Fraction(p["threshold"]),
            )
            for p in args["conditions"]
        }
        outcome = DIMENSION_MAP[ALIASES[args["outcomes"][0]]]
        matches = [
            f
            for f in result.findings
            if f.type == "group_difference"
            and f.payload.outcome_dimension == outcome
            and {(c.dimension, c.operator, c.threshold) for c in f.payload.selection.conditions}
            == conditions
        ]
        if len(matches) != 1:
            blocked("AI_FORMAL_DISCOVERY_NOT_QUALIFIED", 422)
        finding = matches[0]
        formal = finding.payload
        stats = next(d for d in formal.dimensions if d.dimension == outcome)
        sel = formal.selection
        values_ok = (
            len(sel.matches) == actual["target_count"]
            and len(formal.remainder) == actual["remainder_count"]
            and len(sel.unevaluable_records) == actual["unevaluable_count"]
            and sel.eligible_count == actual["eligible_denominator"]
            and stats.target.valid_count == actual["target_valid"]
            and stats.remainder.valid_count == actual["remainder_valid"]
            and stats.target.mean == actual["target_mean"]
            and stats.remainder.mean == actual["remainder_mean"]
            and stats.difference == actual["difference"]
            and sel.matched_share.proportion == actual["share_total"]
            and sel.matched_among_eligible.proportion == actual["share_eligible"]
        )
        expected = {
            "target.valid": score_pairs(stats.target.valid_records, outcome),
            "remainder.valid": score_pairs(stats.remainder.valid_records, outcome),
        }
        for p in args["conditions"]:
            dim = DIMENSION_MAP[ALIASES[p["item_id"]]]
            expected["target.condition." + p["item_id"]] = score_pairs(sel.matches, dim)
            expected["remainder.condition." + p["item_id"]] = score_pairs(formal.remainder, dim)
        # The formal confirmed-batch parser never admits missing scores. Keep this
        # explicit; do not replace nonempty row Witnesses with unrelated score refs.
        if sel.unevaluable_records:
            blocked("AI_ROW_WITNESS_REPORT_UNSUPPORTED", 422)
        expected["unevaluable.rows"] = set()
    else:
        blocked("AI_REPORT_SEMANTIC_ADAPTER_UNAVAILABLE", 422)
    if not values_ok or data.scope.scope_record_count != actual["denominator"]:
        blocked("AI_FORMAL_VALUES_CONFLICT", 503)
    if actual_sources != expected:
        blocked("AI_FORMAL_SOURCES_CONFLICT", 503)
    return finding


async def submit(
    service: TaskService,
    actor: Principal,
    batch_id: str,
    task_id: str,
    body: ReviewSubmission,
    *,
    request: Request | None = None,
) -> ReviewReceipt:
    if len(set(body.fact_ids)) != len(body.fact_ids):
        blocked("AI_DUPLICATE_FACT")
    row = await run_in_threadpool(service._row, actor, batch_id, task_id, body.snapshot_id)
    with service.review_submission(task_id):
        return await _submit_review(service, actor, row, body, request=request)


async def _submit_review(
    service: TaskService,
    actor: Principal,
    row: dict,
    body: ReviewSubmission,
    *,
    request: Request | None,
) -> ReviewReceipt:
    cancelled = cancellation(request)
    check_cancelled(cancelled)
    source, executor = await run_in_threadpool(service.replay, actor, row)
    check_cancelled(cancelled)
    facts = []
    for fact_id in body.fact_ids:
        fact = executor.facts.get(fact_id)
        if fact is None or not fact.view["qualified"]:
            blocked("AI_FACT_NOT_ELIGIBLE", 422)
        facts.append(fact)
    batch_id, task_id = row["batch_id"], row["task_id"]
    if row["review_json"]:
        return await run_in_threadpool(_read_receipt, service, actor, task_id, body)

    reader = DiscoveryReader(service.store.database, clock=service.provider.service.clock)
    spec = DiscoverySpec(snapshot_id=body.snapshot_id)
    async with reader.calculation(actor, batch_id, spec, request=request, cancelled=cancelled) as (
        data,
        result,
    ):

        def prepare() -> tuple[DiscoveryResultService, AnalysisCreate, AnalysisSnapshot]:
            mapped = {fact.fact_id: match_fact(fact, data, result).discovery_id for fact in facts}
            create = AnalysisCreate(
                snapshot_id=body.snapshot_id,
                title="AI调查事实待教师核查",
                include_dimensions=False,
                discovery=DiscoverySelection(
                    input_key=data.scope.input_key, discovery_ids=tuple(mapped.values())
                ),
            )
            database = ReviewDatabase(service.store, row, actor, mapped, cancelled)
            results = DiscoveryResultService(database, clock=service.provider.service.clock)
            snapshot = results.prepare_discovery(
                reader, actor, create, data, result, cancelled=cancelled
            )
            return results, create, snapshot

        results, create, snapshot = await run_in_threadpool(prepare)
    await run_in_threadpool(source.validate)
    await run_in_threadpool(
        results._save, actor, batch_id, create, snapshot, expected_input=data, cancelled=cancelled
    )
    return await run_in_threadpool(_read_receipt, service, actor, task_id, body)


def _read_receipt(
    service: TaskService, actor: Principal, task_id: str, body: ReviewSubmission
) -> ReviewReceipt:
    with service.store.database.transaction() as c:
        current = service.store._row(c, task_id)
        service.store.check(c, current, actor)
        raw_receipt = current["review_json"]
        if raw_receipt is None:
            blocked("AI_REVIEW_CONFLICT")
        receipt = json.loads(raw_receipt)
        if receipt.get("fact_ids") is not None or "preparing" in receipt:
            blocked("AI_REVIEW_IN_PROGRESS")
        event = c.execute(
            "SELECT payload FROM ai_synthetic_events WHERE task_id=? AND "
            "phase='review_requested' ORDER BY sequence DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    if event is None or set(json.loads(event[0])["fact_ids"]) != set(body.fact_ids):
        blocked("AI_REVIEW_CONFLICT")
    return ReviewReceipt.model_validate_json(raw_receipt)
