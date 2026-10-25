"""Export preflight and private, bounded request diagnostics."""

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.core.errors import APIError
from app.edu.auth import deny, require_teacher
from app.edu.exports import ResultExporter
from app.edu.query import read_query
from app.edu.service import stamp
from app.edu.support_models import ExportCheck, ExportCheckRequest, RequestTrace
from app.storage.request_traces import RequestTraceStore

from .edu import Actor, Backend
from .edu_reviews import ERRORS, body_schema

router = APIRouter(prefix="/api/v1/edu", tags=["EDU support"])


@router.post(
    "/batches/{batch_id}/analysis-results/{result_id}/export-check",
    response_model=ExportCheck,
    response_model_exclude_none=True,
    responses=ERRORS,
    openapi_extra=body_schema(ExportCheckRequest),
)
async def export_check(
    batch_id: str, result_id: str, request: Request, actor: Actor, backend: Backend
) -> ExportCheck:
    body = await read_query(request, ExportCheckRequest)
    exporter = ResultExporter(backend.database, clock=backend.clock)
    return await run_in_threadpool(exporter.check, actor, batch_id, result_id, **body.model_dump())


@router.get("/diagnostics/requests/{request_id}", response_model=RequestTrace, responses=ERRORS)
def request_trace(
    request_id: str, request: Request, actor: Actor, backend: Backend
) -> RequestTrace:
    # Recheck current credential and teacher status inside the business read transaction.
    with backend.database.transaction() as connection:
        require_teacher(connection, actor, stamp(backend.clock()))
        path = request.app.state.settings.request_trace_database_path
        if path is None:
            raise APIError(503, "REQUEST_TRACE_UNAVAILABLE", "请求诊断记录暂不可用")
        try:
            row = RequestTraceStore(path).get(actor.teacher_id, request_id)
        except (ValueError, OSError):
            raise APIError(503, "REQUEST_TRACE_UNAVAILABLE", "请求诊断记录暂不可用") from None
        if row is None:
            deny(404)
        return RequestTrace(**row)
