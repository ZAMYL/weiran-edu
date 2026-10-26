"""Controlled formal discovery endpoints; existing teacher authentication is reused."""

from fastapi import APIRouter, Request
from starlette.requests import ClientDisconnect

from app.core.errors import APIError
from app.edu.discoveries import DiscoveryReader
from app.edu.discovery_models import (
    DiscoveryChart,
    DiscoveryDetail,
    DiscoveryList,
    DiscoveryQuery,
    DiscoveryRecords,
    DiscoveryRecordsQuery,
    DiscoverySpec,
)
from app.edu.query import read_query, request_schema
from app.schemas.common import ErrorResponse

from .edu import INSIGHT_ERRORS, Actor, Backend

router = APIRouter(prefix="/api/v1/edu/batches/{batch_id}/discoveries", tags=["EDU发现"])
DISCOVERY_ERRORS = {**INSIGHT_ERRORS, 499: {"model": ErrorResponse}}


def schema(model) -> dict:
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": request_schema(model)}},
        }
    }


async def _query(
    batch_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
    model,
    operation: str,
    discovery_id: str | None = None,
):
    try:
        query = await read_query(request, model)
    except ClientDisconnect:
        raise APIError(499, "DISCOVERY_CANCELLED", "请求已断开，发现计算已取消") from None
    except APIError as error:
        if error.code == "QUERY_TOO_LARGE":
            raise APIError(413, "REQUEST_TOO_LARGE", "发现请求超过大小限制") from None
        raise
    reader = DiscoveryReader(backend.database, clock=backend.clock)
    return await reader.query(
        actor, batch_id, query, operation=operation, discovery_id=discovery_id, request=request
    )


@router.post(
    "/query",
    response_model=DiscoveryList,
    responses=DISCOVERY_ERRORS,
    openapi_extra=schema(DiscoveryQuery),
)
async def discoveries(batch_id: str, request: Request, actor: Actor, backend: Backend):
    return await _query(batch_id, request, actor, backend, DiscoveryQuery, "list")


@router.post(
    "/{discovery_id}/query",
    response_model=DiscoveryDetail,
    responses=DISCOVERY_ERRORS,
    openapi_extra=schema(DiscoverySpec),
)
async def detail(
    batch_id: str, discovery_id: str, request: Request, actor: Actor, backend: Backend
):
    return await _query(batch_id, request, actor, backend, DiscoverySpec, "detail", discovery_id)


@router.post(
    "/{discovery_id}/chart/query",
    response_model=DiscoveryChart,
    responses=DISCOVERY_ERRORS,
    openapi_extra=schema(DiscoverySpec),
)
async def chart(batch_id: str, discovery_id: str, request: Request, actor: Actor, backend: Backend):
    return await _query(batch_id, request, actor, backend, DiscoverySpec, "chart", discovery_id)


@router.post(
    "/{discovery_id}/records/query",
    response_model=DiscoveryRecords,
    responses=DISCOVERY_ERRORS,
    openapi_extra=schema(DiscoveryRecordsQuery),
)
async def records(
    batch_id: str, discovery_id: str, request: Request, actor: Actor, backend: Backend
):
    return await _query(
        batch_id, request, actor, backend, DiscoveryRecordsQuery, "records", discovery_id
    )
