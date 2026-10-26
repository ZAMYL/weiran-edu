"""Formal local-only discovery service; scope, kernel facts and DB evidence stay distinct."""

import json
import re
import sys
from contextlib import asynccontextmanager
from time import perf_counter

import anyio
from fastapi import Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.analysis.edu.discovery import BoundaryRequest, Policy, present, sources
from app.analysis.edu.discovery.models import VERSION, Discovery, DiscoveryResult
from app.analysis.edu.models import AnalysisInputError, Condition, Dimension, Operator

from . import discovery_bridge as bridge
from . import discovery_worker as worker
from .analysis import CODE_MAP, DIMENSION_MAP, OPERATOR_MAP
from .auth import Principal, deny
from .discovery_bridge import DiscoveryInput, DiscoveryInputReader
from .discovery_models import (
    DiscoveryChart,
    DiscoveryDetail,
    DiscoveryList,
    DiscoveryQuery,
    DiscoveryRecords,
    DiscoveryRecordsQuery,
    DiscoverySpec,
    DiscoverySummary,
)
from .service import fail

POLICY = Policy(search_budget=20, maximum_findings=50, maximum_students=bridge.MAX_RECORDS)
OPERATORS = {value.value: key for key, value in OPERATOR_MAP.items()}
DETAIL_FIELDS = frozenset(DiscoveryDetail.model_fields) - {"scope", "input_key"}


def _code(value: str) -> str:
    return CODE_MAP[Dimension[value]]


def project(finding: Discovery, data: DiscoveryInput) -> DiscoveryDetail:
    """Explicit per-kind translation followed by nested whitelist DTO validation."""
    view = present(finding)
    view = {key: value for key, value in view.items() if key in DETAIL_FIELDS}
    view["scope"] = data.scope.model_dump(mode="json")
    view["input_key"] = data.scope.input_key
    view["conditions"] = [
        {
            "dimension_code": _code(c["dimension"]),
            "operator": OPERATORS[c["operator"]],
            "threshold": c["threshold"],
        }
        for c in view["conditions"]
    ]
    metrics = dict(view["metrics"])
    if finding.type in {"dimension_relation", "rare_combination"}:
        metrics["left"], metrics["right"] = _code(metrics["left"]), _code(metrics["right"])
    elif finding.type == "group_difference":
        metrics["outcome_dimension"] = _code(metrics["outcome_dimension"])
        view["chart"] = [
            {
                "dimension_code": _code(c["dimension"]),
                **{k: v for k, v in c.items() if k != "dimension"},
            }
            for c in view["chart"]
        ]
    elif finding.type == "boundary_sensitivity":
        view["chart"] = [
            {"operator": OPERATORS[c["operator"]], "count": c["count"]} for c in view["chart"]
        ]
    view["metrics"] = metrics
    # JSON mode accepts JSON arrays as tuples while preserving strict nested scalar types.
    return DiscoveryDetail.model_validate_json(json.dumps(view, ensure_ascii=False))


def _boundaries(spec: DiscoverySpec) -> tuple[BoundaryRequest, ...]:
    conditions = tuple(
        Condition(DIMENSION_MAP[c.dimension_code], OPERATOR_MAP[c.operator], c.value)
        for c in spec.scope_conditions
    )
    return tuple(
        BoundaryRequest(conditions, c.dimension)
        for c in conditions
        if c.operator in (Operator.LT, Operator.LE)
    )


@asynccontextmanager
async def _snapshot(
    reader: DiscoveryInputReader, actor: Principal, batch_id: str, spec: DiscoverySpec
):
    context = reader.snapshot(actor, batch_id, spec)
    with anyio.CancelScope(shield=True):
        data = await run_in_threadpool(context.__enter__)
    try:
        yield data
    except BaseException:
        error = sys.exc_info()
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(context.__exit__, *error)
        raise
    else:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(context.__exit__, None, None, None)


class DiscoveryReader(DiscoveryInputReader):
    async def query(
        self,
        actor: Principal,
        batch_id: str,
        query: DiscoverySpec,
        *,
        operation: str,
        discovery_id: str | None = None,
        request: Request | None = None,
    ) -> DiscoveryList | DiscoveryDetail | DiscoveryChart | DiscoveryRecords:
        started = perf_counter()
        disconnect = getattr(request.state, "edu_discovery_disconnect", None) if request else None

        async def disconnected() -> bool:
            if disconnect is not None:
                return disconnect.is_set()
            return await request.is_disconnected() if request else False

        async with _snapshot(self, actor, batch_id, query) as data:
            if discovery_id is not None and not re.fullmatch(r"[0-9a-f]{24}", discovery_id):
                deny(404)
            try:
                result = await worker.execute(
                    data.dataset,
                    batch_id,
                    query.snapshot_id,
                    data.student_ids,
                    _boundaries(data.spec),
                    POLICY,
                    timeout=max(0, bridge.MAX_SECONDS - (perf_counter() - started)),
                    disconnected=disconnected,
                )
                return await run_in_threadpool(
                    self._response, actor, data, result, query, operation, discovery_id
                )
            except (AnalysisInputError, ValidationError, KeyError, TypeError, ValueError):
                fail("DATA_INTEGRITY_ERROR", 503)

    def _response(
        self,
        actor: Principal,
        data: DiscoveryInput,
        result: DiscoveryResult,
        query: DiscoverySpec,
        operation: str,
        discovery_id: str | None,
    ) -> DiscoveryList | DiscoveryDetail | DiscoveryChart | DiscoveryRecords:
        expected_refs = tuple(
            r.reference for r in data.dataset.records if r.reference.student_id in data.student_ids
        )
        if (
            result.context.batch_id != data.scope.batch_id
            or result.context.snapshot_id != data.scope.snapshot_id
            or result.context.scope.student_ids != data.student_ids
            or result.context.scope.records != expected_refs
            or result.policy != POLICY
            or len(result.findings) > POLICY.maximum_findings
            or result.qualified_count < len(result.findings)
            or len({f.discovery_id for f in result.findings}) != len(result.findings)
            or any(
                f.context != result.context or f.policy != POLICY or f.rule_version != VERSION
                for f in result.findings
            )
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        if operation == "list":
            assert isinstance(query, DiscoveryQuery)
            # Only after full computation/sorting/retention do we select a result page.
            cards = tuple(
                DiscoverySummary.model_validate(
                    project(finding, data).model_dump(include=set(DiscoverySummary.model_fields))
                )
                for finding in result.findings
            )
            return self.page(
                data.scope,
                cards,
                qualified_count=result.qualified_count,
                rule_version=VERSION,
                offset=query.offset,
                limit=query.limit,
            )
        finding = next((f for f in result.findings if f.discovery_id == discovery_id), None)
        if finding is None:
            deny(404)
        detail = project(finding, data)
        if operation == "detail":
            return self.bounded(detail)
        if operation == "chart":
            return self.bounded(
                DiscoveryChart.model_validate(
                    detail.model_dump(include=set(DiscoveryChart.model_fields))
                )
            )
        assert operation == "records" and isinstance(query, DiscoveryRecordsQuery)
        selected = next((s for s in finding.sources if s.metric_key == query.metric_key), None)
        if selected is None:
            deny(404)
        # First the kernel verifies this metric's actual participants and dimensions.
        checked = sources(finding, query.metric_key)
        if any(
            len(evidence) != len(selected.dimensions)
            or {e.dimension for e in evidence} != set(selected.dimensions)
            for _, evidence in checked
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        # Then the existing bridge verifies every canonical reference against the current
        # formal DB and only afterward pages the authorized anonymous score links.
        participants = self.resolve_sources(
            actor,
            data,
            tuple(r for r, _ in checked),
            selected.dimensions,
            metric_key=query.metric_key,
            offset=query.offset,
            limit=query.limit,
        )
        return self.bounded(
            DiscoveryRecords(
                **participants.model_dump(),
                discovery_id=finding.discovery_id,
                rule_version=VERSION,
                conditions=detail.conditions,
                source_dimensions=tuple(CODE_MAP[d] for d in selected.dimensions),
            )
        )
