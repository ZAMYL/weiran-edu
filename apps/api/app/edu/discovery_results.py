"""Persist selected deterministic discoveries through existing teacher result versions."""

from fractions import Fraction

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.analysis.edu.discovery import sources
from app.analysis.edu.discovery.models import DiscoveryResult
from app.core.errors import APIError

from .analysis import stat
from .auth import Principal, deny
from .discoveries import DiscoveryReader, project
from .discovery_bridge import DiscoveryInput
from .discovery_disconnect import CancelCheck, cancellation, check_cancelled
from .discovery_models import DiscoveryDetail, DiscoverySpec
from .insight_models import InsightSpec
from .results import ResultService
from .review_models import AnalysisCreate, AnalysisResult, AnalysisSnapshot, SavedDiscoverySnapshot
from .reviews import require_owner
from .service import fail


def checked_findings(
    reader: DiscoveryReader,
    principal: Principal,
    data: DiscoveryInput,
    result: DiscoveryResult,
    ids: tuple[str, ...],
    *,
    cancelled: CancelCheck | None = None,
) -> tuple[DiscoveryDetail, ...]:
    available = {f.discovery_id: f for f in result.findings}
    if any(identifier not in available for identifier in ids):
        deny(404)
    checked = set()
    findings = tuple(available[identifier] for identifier in ids)
    for finding in findings:
        check_cancelled(cancelled)
        for metric in finding.sources:
            check_cancelled(cancelled)
            resolved = sources(finding, metric.metric_key)
            if any(
                len(evidence) != len(metric.dimensions)
                or {e.dimension for e in evidence} != set(metric.dimensions)
                for _, evidence in resolved
            ):
                fail("DATA_INTEGRITY_ERROR", 503)
            refs = tuple(reference for reference, _ in resolved)
            key = (refs, metric.dimensions)
            if key not in checked:
                reader.resolve_sources(
                    principal,
                    data,
                    refs,
                    metric.dimensions,
                    metric_key=metric.metric_key,
                    limit=1,
                )
                checked.add(key)
    check_cancelled(cancelled)
    return tuple(project(f, data) for f in findings)


class DiscoveryResultService(ResultService):
    async def verify_saved(
        self, principal: Principal, saved: AnalysisResult, *, cancelled: CancelCheck | None = None
    ) -> None:
        original = saved.analysis.discovery
        assert original is not None
        reader = DiscoveryReader(self.database, clock=self.clock)
        async with reader.calculation(
            principal, saved.batch_id, original.spec, cancelled=cancelled
        ) as (data, result):
            if any(
                f.discovery_id not in {r.discovery_id for r in result.findings}
                for f in original.findings
            ):
                fail("DATA_INTEGRITY_ERROR", 503)
            projected = await run_in_threadpool(
                checked_findings,
                reader,
                principal,
                data,
                result,
                tuple(f.discovery_id for f in original.findings),
                cancelled=cancelled,
            )
            if data.scope != original.scope or projected != original.findings:
                fail("DATA_INTEGRITY_ERROR", 503)

    async def create_discovery(
        self,
        principal: Principal,
        batch_id: str,
        body: AnalysisCreate,
        *,
        request: Request | None = None,
    ) -> AnalysisResult:
        selection = body.discovery
        assert selection is not None
        cancelled = cancellation(request)

        def authorize():
            with self.database.transaction() as connection:
                batch = self._batch(connection, principal, batch_id, body.snapshot_id)
                require_owner(batch, principal)

        # Read-only grants cannot allocate a worker or persist teacher judgments.
        await run_in_threadpool(authorize)
        reader = DiscoveryReader(self.database, clock=self.clock)
        spec = DiscoverySpec(snapshot_id=body.snapshot_id, scope_conditions=body.conditions)
        async with reader.calculation(
            principal, batch_id, spec, request=request, cancelled=cancelled
        ) as (data, result):
            if selection.input_key != data.scope.input_key:
                raise APIError(409, "DISCOVERY_INPUT_MISMATCH", "分析范围已变化，请重新获取发现")

            def prepare() -> AnalysisSnapshot:
                findings = checked_findings(
                    reader, principal, data, result, selection.discovery_ids, cancelled=cancelled
                )
                ids = set(data.student_ids)
                members = tuple(s for s in data.students if s.student_id in ids)
                with self.database.transaction() as connection:
                    batch = self._batch(connection, principal, batch_id, body.snapshot_id)
                    require_owner(batch, principal)
                    statistics = self._statistics(batch, data.dataset)
                return AnalysisSnapshot(
                    query=InsightSpec(
                        snapshot_id=body.snapshot_id,
                        conditions=data.spec.scope_conditions,
                        include_dimensions=False,
                    ),
                    statistics=statistics,
                    group_statistics=data.scope.statistics if body.conditions else None,
                    total_count=len(data.students),
                    matched_count=len(members),
                    ratio=stat(Fraction(len(members), len(data.students)))
                    if data.students
                    else stat(None),
                    members=members,
                    candidates=(),
                    discovery=SavedDiscoverySnapshot(
                        spec=data.spec,
                        scope=data.scope,
                        findings=findings,
                    ),
                )

            snapshot = await run_in_threadpool(prepare)
        # Exit the shared bounded snapshot (including revoke/corruption checks) BEFORE write.
        return await run_in_threadpool(
            self._save,
            principal,
            batch_id,
            body,
            snapshot,
            expected_input=data,
            cancelled=cancelled,
        )
