"""Authenticated reviewed text, saved judgments and private export routes."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app.core.errors import APIError
from app.edu.discovery_disconnect import cancellation
from app.edu.discovery_results import DiscoveryResultService
from app.edu.exports import REPORT_FORMATS, ResultExporter
from app.edu.query import read_query, request_schema
from app.edu.results import ResultService
from app.edu.review_models import (
    AnalysisCreate,
    AnalysisList,
    AnalysisPatch,
    AnalysisResult,
    TextKind,
    TextReview,
    TextReviewPreview,
    TextReviewWrite,
)
from app.edu.reviews import TextReviewer
from app.edu.support_models import ExportFormat
from app.schemas.common import ErrorResponse

from .edu import Actor, Backend, Limit, Offset

router = APIRouter(prefix="/api/v1/edu/batches/{batch_id}", tags=["EDU reviews and results"])
ERRORS = {code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 413, 415, 422, 499, 503)}


def body_schema(model: type) -> dict:
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": request_schema(model)}},
        }
    }


@router.get("/text-reviews/{kind}/{target_id}", response_model=TextReviewPreview, responses=ERRORS)
def text_preview(
    batch_id: str,
    kind: TextKind,
    target_id: str,
    actor: Actor,
    backend: Backend,
    snapshot_id: Annotated[str, Query(min_length=1, max_length=128)],
) -> TextReviewPreview:
    return TextReviewer(backend.database, clock=backend.clock).preview(
        actor, batch_id, kind, target_id, snapshot_id=snapshot_id
    )


@router.put(
    "/text-reviews/{kind}/{target_id}",
    response_model=TextReview,
    responses=ERRORS,
    openapi_extra=body_schema(TextReviewWrite),
)
async def text_review(
    batch_id: str,
    kind: TextKind,
    target_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
) -> TextReview:
    body = await read_query(request, TextReviewWrite)
    reviewer = TextReviewer(backend.database, clock=backend.clock)
    return await run_in_threadpool(reviewer.review, actor, batch_id, kind, target_id, body)


@router.post(
    "/analysis-results",
    response_model=AnalysisResult,
    status_code=201,
    responses=ERRORS,
    openapi_extra=body_schema(AnalysisCreate),
)
async def create_result(
    batch_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
) -> AnalysisResult:
    try:
        body = await read_query(request, AnalysisCreate)
    except ClientDisconnect:
        raise APIError(499, "DISCOVERY_CANCELLED", "请求已断开，保存已取消") from None
    if body.discovery is not None:
        return await DiscoveryResultService(backend.database, clock=backend.clock).create_discovery(
            actor, batch_id, body, request=request
        )
    service = ResultService(backend.database, clock=backend.clock)
    return await run_in_threadpool(service.create, actor, batch_id, body)


@router.get("/analysis-results", response_model=AnalysisList, responses=ERRORS)
def list_results(
    batch_id: str,
    actor: Actor,
    backend: Backend,
    snapshot_id: str | None = None,
    offset: Offset = 0,
    limit: Limit = 20,
) -> AnalysisList:
    return ResultService(backend.database, clock=backend.clock).list(
        actor, batch_id, snapshot_id=snapshot_id, offset=offset, limit=limit
    )


@router.get("/analysis-results/{result_id}", response_model=AnalysisResult, responses=ERRORS)
def get_result(
    batch_id: str,
    result_id: str,
    actor: Actor,
    backend: Backend,
    snapshot_id: str | None = None,
) -> AnalysisResult:
    return ResultService(backend.database, clock=backend.clock).get(
        actor, batch_id, result_id, snapshot_id=snapshot_id
    )


@router.patch(
    "/analysis-results/{result_id}",
    response_model=AnalysisResult,
    responses=ERRORS,
    openapi_extra=body_schema(AnalysisPatch),
)
async def patch_result(
    batch_id: str,
    result_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
) -> AnalysisResult:
    body = await read_query(request, AnalysisPatch)
    service = ResultService(backend.database, clock=backend.clock)
    return await run_in_threadpool(service.patch, actor, batch_id, result_id, body)


@router.get(
    "/analysis-results/{result_id}/export",
    response_class=Response,
    responses={
        **ERRORS,
        200: {
            "description": "授权内部下载；格式由查询参数选择",
            "content": {
                "application/json": {"schema": {"type": "object"}},
                **{
                    mime: {"schema": {"type": "string", "format": "binary"}}
                    for mime in ("application/zip", *REPORT_FORMATS.values())
                },
            },
        },
    },
)
def export_result(
    batch_id: str,
    result_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
    snapshot_id: Annotated[str, Query(min_length=1, max_length=128)],
    expected_version: Annotated[int, Query(ge=1)],
    format: ExportFormat = "zip",
) -> Response:
    content, media_type, filename = ResultExporter(backend.database, clock=backend.clock).export(
        actor,
        batch_id,
        result_id,
        snapshot_id=snapshot_id,
        expected_version=expected_version,
        format=format,
        cancelled=cancellation(request),
    )
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
