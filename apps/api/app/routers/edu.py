"""Authenticated pilot API; management and credential issuance stay operator-only."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.responses import PlainTextResponse

from app.core.errors import APIError
from app.edu.auth import Principal, authenticate, deny, require_teacher
from app.edu.models import (
    Batch,
    BatchList,
    CommitRequest,
    CommitResult,
    Evidence,
    ImportMetadata,
    ImportPatch,
    ImportView,
    Model,
    StudentEvidence,
    StudentList,
)
from app.edu.reads import BatchReader
from app.edu.service import EduService, stamp
from app.importing.edu.report import render_quality_report
from app.importing.edu.types import ImportFileError, ParseLimits

router = APIRouter(prefix="/api/v1/edu", tags=["EDU"])
bearer = HTTPBearer(auto_error=False)
type Offset = Annotated[int, Query(ge=0)]
type Limit = Annotated[int, Query(ge=1, le=100)]


def service(request: Request) -> EduService:
    return request.app.state.edu_service


def principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        deny(401, "UNAUTHORIZED")
    return authenticate(service(request).database, credentials.credentials)


type Actor = Annotated[Principal, Depends(principal)]
type Backend = Annotated[EduService, Depends(service)]


def _parse_failure(exc: ImportFileError) -> APIError:
    if exc.code == "FILE_SIZE_LIMIT":
        return APIError(413, "IMPORT_FILE_TOO_LARGE", "上传文件超过限制")
    if exc.code in {"PARSE_TIME_LIMIT", "PARSER_BUSY", "PARSER_UNAVAILABLE"}:
        return APIError(503, "IMPORT_PROCESSING_UNAVAILABLE", "解析服务暂不可用，请稍后重试")
    return APIError(422, "IMPORT_FILE_INVALID", "文件不符合当前Excel模板或解析限制")


@router.get("/me")
def me(actor: Actor) -> dict[str, str]:
    return {"teacher_id": actor.teacher_id}


class Scope(Model):
    data_scope_id: str
    label: str
    can_import: bool


class Scopes(Model):
    items: tuple[Scope, ...]


@router.get("/scopes", response_model=Scopes)
def scopes(actor: Actor, backend: Backend) -> Scopes:
    with backend.database.transaction() as connection:
        require_teacher(connection, actor, stamp(backend.clock()))
        rows = connection.execute(
            "SELECT s.data_scope_id,s.label,m.can_import FROM data_scopes s JOIN scope_members m "
            "ON m.data_scope_id=s.data_scope_id WHERE m.teacher_id=? ORDER BY s.data_scope_id",
            (actor.teacher_id,),
        ).fetchall()
        return Scopes(
            items=tuple(
                Scope(
                    data_scope_id=r["data_scope_id"],
                    label=r["label"],
                    can_import=bool(r["can_import"]),
                )
                for r in rows
            )
        )


async def _upload(request: Request) -> tuple[bytes, ImportMetadata]:
    # Authenticate before reading multipart. Bound actual streamed bytes, not just Content-Length.
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "multipart/form-data":
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "请使用Excel文件上传表单")
    maximum = ParseLimits().compressed_bytes + 64 * 1024
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise APIError(413, "IMPORT_FILE_TOO_LARGE", "上传文件超过限制")
        body.extend(chunk)
    consumed = False

    async def receive() -> dict:
        nonlocal consumed
        if consumed:
            return {"type": "http.disconnect"}
        consumed = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    limited = Request(request.scope, receive)
    try:
        async with limited.form(max_files=1, max_fields=5, max_part_size=4096) as form:
            items = list(form.multi_items())
            keys = [key for key, _ in items]
            allowed = {
                "file",
                "data_scope_id",
                "title",
                "course",
                "assessment_date",
                "revision_of_batch_id",
            }
            if len(keys) != len(set(keys)) or set(keys) - allowed:
                raise APIError(422, "VALIDATION_ERROR", "上传字段不符合要求")
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise APIError(422, "VALIDATION_ERROR", "缺少Excel文件")
            if not (file.filename or "").lower().endswith(".xlsx") or file.content_type not in {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
            }:
                raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "仅支持当前xlsx模板")
            metadata = ImportMetadata.model_validate(
                {key: value for key, value in items if key != "file"}
            )
            data = await file.read(ParseLimits().compressed_bytes + 1)
            if len(data) > ParseLimits().compressed_bytes:
                raise APIError(413, "IMPORT_FILE_TOO_LARGE", "上传文件超过限制")
            return data, metadata
    except APIError:
        raise
    except ValidationError:
        raise APIError(422, "VALIDATION_ERROR", "上传字段不符合要求") from None
    except Exception:
        raise APIError(422, "IMPORT_FILE_INVALID", "上传表单无法读取") from None


@router.post(
    "/imports/preview",
    response_model=ImportView,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["file", "data_scope_id", "title"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "data_scope_id": {"type": "string", "maxLength": 128},
                            "title": {"type": "string", "minLength": 1, "maxLength": 100},
                            "course": {"type": "string", "minLength": 1, "maxLength": 100},
                            "assessment_date": {"type": "string", "format": "date"},
                            "revision_of_batch_id": {"type": "string", "maxLength": 128},
                        },
                    }
                }
            },
        },
    },
)
async def preview(request: Request, actor: Actor, backend: Backend) -> ImportView:
    data, metadata = await _upload(request)
    try:
        return await run_in_threadpool(backend.create_import, actor, data, metadata)
    except ImportFileError as exc:
        raise _parse_failure(exc) from None


@router.get("/imports/{import_id}", response_model=ImportView)
def get_import(
    import_id: str,
    actor: Actor,
    backend: Backend,
    row_offset: Offset = 0,
    row_limit: Limit = 25,
    student_offset: Offset = 0,
    student_limit: Limit = 20,
    issue_offset: Offset = 0,
    issue_limit: Limit = 50,
) -> ImportView:
    return backend.get_import(
        actor,
        import_id,
        row_offset=row_offset,
        row_limit=row_limit,
        student_offset=student_offset,
        student_limit=student_limit,
        issue_offset=issue_offset,
        issue_limit=issue_limit,
    )


@router.patch("/imports/{import_id}", response_model=ImportView)
def patch_import(import_id: str, patch: ImportPatch, actor: Actor, backend: Backend) -> ImportView:
    return backend.patch_import(actor, import_id, patch)


@router.post("/imports/{import_id}/commit", response_model=CommitResult, status_code=201)
def commit_import(
    import_id: str,
    body: CommitRequest,
    actor: Actor,
    backend: Backend,
    response: Response,
) -> CommitResult:
    try:
        result = backend.commit(actor, import_id, body)
    except ImportFileError as exc:
        raise _parse_failure(exc) from None
    response.status_code = 201 if result.result == "created" else 200
    return result


@router.get("/imports/{import_id}/quality-report")
def quality_report(
    import_id: str, actor: Actor, backend: Backend, format: Literal["json", "markdown"] = "json"
) -> Response:
    report = backend.quality_report(actor, import_id)
    if format == "markdown":
        return PlainTextResponse(render_quality_report(report), media_type="text/markdown")
    return Response(report.model_dump_json(), media_type="application/json")


@router.get("/batches", response_model=BatchList)
def batches(
    actor: Actor,
    backend: Backend,
    data_scope_id: str | None = None,
    include_revisions: bool = False,
    offset: Offset = 0,
    limit: Limit = 20,
) -> BatchList:
    return backend.list_batches(
        actor,
        data_scope_id=data_scope_id,
        include_revisions=include_revisions,
        offset=offset,
        limit=limit,
    )


@router.get("/batches/{batch_id}", response_model=Batch)
def batch(batch_id: str, actor: Actor, backend: Backend) -> Batch:
    return backend.get_batch(actor, batch_id)


@router.get("/batches/{batch_id}/students", response_model=StudentList)
def students(
    batch_id: str,
    actor: Actor,
    backend: Backend,
    offset: Offset = 0,
    limit: Limit = 20,
    snapshot_id: str | None = None,
) -> StudentList:
    return BatchReader(backend.database, clock=backend.clock).students(
        actor,
        batch_id,
        offset=offset,
        limit=limit,
        snapshot_id=snapshot_id,
    )


@router.get("/batches/{batch_id}/students/{student_id}/evidence", response_model=StudentEvidence)
def student_evidence(
    batch_id: str, student_id: str, actor: Actor, backend: Backend
) -> StudentEvidence:
    return BatchReader(backend.database, clock=backend.clock).student_evidence(
        actor, batch_id, student_id
    )


@router.get("/batches/{batch_id}/evidence/{evidence_id}", response_model=Evidence)
def evidence(batch_id: str, evidence_id: str, actor: Actor, backend: Backend) -> Evidence:
    return BatchReader(backend.database, clock=backend.clock).evidence(actor, batch_id, evidence_id)
