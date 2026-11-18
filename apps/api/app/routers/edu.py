"""Authenticated pilot API; management and credential issuance stay operator-only."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.responses import PlainTextResponse

from app.core.errors import APIError
from app.core.exact_json import ExactJSONResponse
from app.edu.analysis import AnalysisReader
from app.edu.analysis_models import DimensionCode, GroupResult, Statistics
from app.edu.assessment_models import (
    AssessmentOverview,
    AssessmentResult,
    ParticipantList,
    StudentCodeEvidence,
)
from app.edu.assessments import AssessmentReader, AssessmentService
from app.edu.auth import Principal, authenticate, deny, require_teacher
from app.edu.diagnostic_models import Action, BatchCapabilities, IntegrityReport, IntegrityRequest
from app.edu.diagnostics import DiagnosticReader
from app.edu.insight_models import (
    InsightQuery,
    InsightRecordsQuery,
    InsightRecordsResult,
    InsightResult,
)
from app.edu.insights import InsightReader
from app.edu.models import (
    Batch,
    BatchArchive,
    BatchList,
    CommitRequest,
    CommitResult,
    Evidence,
    ImportPatch,
    ImportView,
    Model,
    Rubric,
    StudentEvidence,
    StudentList,
)
from app.edu.query import group_query, group_schema, read_query, request_schema
from app.edu.reads import BatchReader
from app.edu.service import EduService, stamp
from app.edu.summary import BatchSummary
from app.edu.upload_metadata import UploadMetadata, filename_title, resolve_upload_metadata
from app.importing.edu.adapter import adapter_for
from app.importing.edu.answersheet import parse_answersheet
from app.importing.edu.detect import ZIP_HTML, detect_asset
from app.importing.edu.report import render_quality_report
from app.importing.edu.types import ImportFileError, ParseLimits
from app.schemas.common import ErrorResponse

router = APIRouter(prefix="/api/v1/edu", tags=["EDU"], default_response_class=ExactJSONResponse)
bearer = HTTPBearer(auto_error=False)
INSIGHT_ERRORS = {
    status: {"model": ErrorResponse} for status in (401, 404, 409, 413, 415, 422, 500, 503)
}
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
    actor = authenticate(service(request).database, credentials.credentials)
    request.state.trace_teacher_id = actor.teacher_id
    return actor


type Actor = Annotated[Principal, Depends(principal)]
type Backend = Annotated[EduService, Depends(service)]


def assessments_of(request: Request) -> AssessmentService:
    return request.app.state.assessment_service


def assessment_reader_of(request: Request) -> AssessmentReader:
    return request.app.state.assessment_reader


type Assessments = Annotated[AssessmentService, Depends(assessments_of)]
type Reader = Annotated[AssessmentReader, Depends(assessment_reader_of)]


def _parse_failure(exc: ImportFileError) -> APIError:
    if exc.code == "UNSUPPORTED_SHEETS":
        return APIError(422, "IMPORT_FORMAT_UNSUPPORTED", "当前暂不支持此文件结构，未创建测评批次")
    printing_errors = {
        "INVALID_PRINTER_SETTINGS_PACKAGE": "打印设置的部件或引用不一致，原文件未被修改",
        "UNSUPPORTED_PRINTER_SETTINGS_FORMAT": "打印设置格式暂不在支持范围，原文件未被修改",
        "PRINTER_SETTINGS_SIZE_LIMIT": "打印设置超过安全大小或数量限制，原文件未被修改",
        "INVALID_WORKBOOK_RELATIONSHIP": "工作簿内部关系异常，无法确定安全来源",
    }
    if exc.code in printing_errors:
        status = 413 if exc.code == "PRINTER_SETTINGS_SIZE_LIMIT" else 422
        return APIError(status, exc.code, printing_errors[exc.code])
    if exc.code == "FILE_SIZE_LIMIT":
        return APIError(413, "IMPORT_FILE_TOO_LARGE", "上传文件超过限制")
    if exc.code in {"PARSE_TIME_LIMIT", "PARSER_BUSY", "PARSER_UNAVAILABLE"}:
        return APIError(503, "IMPORT_PROCESSING_UNAVAILABLE", "解析服务暂不可用，请稍后重试")
    return APIError(422, "IMPORT_FILE_INVALID", "文件不符合当前Excel模板或解析限制")


@router.get("/me")
def me(actor: Actor, backend: Backend) -> dict[str, str]:
    with backend.database.transaction() as connection:
        row = connection.execute(
            "SELECT auth_issuer, auth_subject FROM teachers WHERE teacher_id=?",
            (actor.teacher_id,),
        ).fetchone()
    issuer = row["auth_issuer"] if row is not None else ""
    subject = row["auth_subject"] if row is not None else ""
    if subject == "first-use-owner":
        kind, label = "demo_first_use", "合成演示数据（首次上传样例）"
    elif subject.startswith("synthetic"):
        kind, label = "demo_sample", "合成演示数据"
    else:
        kind, label = "teacher", ""
    return {
        "teacher_id": actor.teacher_id,
        "issuer": issuer,
        "subject": subject,
        "identity_kind": kind,
        "data_label": label,
    }


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


PREVIEW_FIELDS = frozenset(
    {"file", "data_scope_id", "title", "course", "assessment_date", "revision_of_batch_id"}
)
ASSESSMENT_FIELDS = frozenset({"file", "data_scope_id", "title", "course", "assessment_date"})


async def _upload(
    request: Request, *, allowed_fields: frozenset[str] = PREVIEW_FIELDS
) -> tuple[bytes, UploadMetadata, str | None, str | None]:
    # Authenticate before reading multipart. Bound actual streamed bytes, not just Content-Length.
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "multipart/form-data":
        raise APIError(415, "UNSUPPORTED_MEDIA_TYPE", "请使用文件上传表单")
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
            if len(keys) != len(set(keys)) or set(keys) - set(allowed_fields):
                raise APIError(422, "VALIDATION_ERROR", "上传字段不符合要求")
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise APIError(422, "VALIDATION_ERROR", "缺少上传文件")
            fields = {key: value for key, value in items if key != "file"}
            if "title" not in fields:
                fields["title"] = filename_title(file.filename or "")
            metadata = UploadMetadata.model_validate(fields)
            data = await file.read(ParseLimits().compressed_bytes + 1)
            if len(data) > ParseLimits().compressed_bytes:
                raise APIError(413, "IMPORT_FILE_TOO_LARGE", "上传文件超过限制")
            return data, metadata, file.filename, file.content_type
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
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "data_scope_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 128,
                                "description": "当前教学空间；省略时仅能自动使用唯一可导入范围",
                            },
                            "title": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 100,
                                "description": "省略时默认使用文件名，上传后可修改",
                            },
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
    data, uploaded, asset_name, asset_content_type = await _upload(request)
    asset = detect_asset(data, content_type=asset_content_type)
    if adapter_for(asset.kind) is None:
        raise APIError(
            422,
            "NO_ADAPTER_FOR_ASSET",
            f"已识别为 {asset.kind}，当前还没有对应解析器",
        )
    metadata = await run_in_threadpool(
        resolve_upload_metadata,
        backend.database,
        actor,
        uploaded,
        now=stamp(backend.clock()),
    )
    try:
        return await run_in_threadpool(
            backend.create_import,
            actor,
            data,
            metadata,
            asset_name=asset_name,
            asset_content_type=asset_content_type,
        )
    except ImportFileError as exc:
        raise _parse_failure(exc) from None


@router.post("/assessments", response_model=AssessmentResult, status_code=201)
async def create_assessment(
    request: Request,
    response: Response,
    actor: Actor,
    assessments: Assessments,
    backend: Backend,
) -> AssessmentResult:
    """答题卡包（ZIP 内多个 HTML 答卷）→ 解析 → 直接生成测评。

    这类资料没有逐行纠错需求，因此不走"预览 + 确认"，一次请求即入库；
    同一文件重复上传返回原测评（duplicate=true），不重复写入。
    """
    data, uploaded, _, asset_content_type = await _upload(request, allowed_fields=ASSESSMENT_FIELDS)
    asset = detect_asset(data, content_type=asset_content_type)
    if asset.kind != ZIP_HTML:
        raise APIError(
            422,
            "NO_ADAPTER_FOR_ASSET",
            f"已识别为 {asset.kind}，答题卡导入只接受含网页答卷的 ZIP",
        )
    metadata = await run_in_threadpool(
        resolve_upload_metadata,
        backend.database,
        actor,
        uploaded,
        now=stamp(backend.clock()),
    )
    try:
        evidence = await run_in_threadpool(parse_answersheet, data)
        summary = await run_in_threadpool(
            assessments.create_answersheet,
            actor,
            data,
            data_scope_id=metadata.data_scope_id,
            title=metadata.title,
            course=metadata.course,
            assessment_date=metadata.assessment_date,
            evidence=evidence,
        )
    except ImportFileError as exc:
        raise _parse_failure(exc) from None
    if summary.duplicate:
        response.status_code = 200
    response.headers["Cache-Control"] = "no-store"
    return AssessmentResult(
        assessment_id=summary.assessment_id,
        student_count=summary.student_count,
        problem_count=summary.problem_count,
        submission_count=summary.submission_count,
        observation_count=summary.observation_count,
        duplicate=summary.duplicate,
    )


@router.get("/assessments/{assessment_id}", response_model=AssessmentOverview)
def get_assessment(assessment_id: str, actor: Actor, reader: Reader) -> AssessmentOverview:
    return reader.overview(actor, assessment_id)


@router.get("/assessments/{assessment_id}/students", response_model=ParticipantList)
def list_assessment_students(assessment_id: str, actor: Actor, reader: Reader) -> ParticipantList:
    return reader.participants(actor, assessment_id)


@router.get(
    "/assessments/{assessment_id}/students/{student_pk}",
    response_model=StudentCodeEvidence,
)
def get_assessment_student(
    assessment_id: str, student_pk: str, actor: Actor, reader: Reader
) -> StudentCodeEvidence:
    """单个学生的全部题目证据：代码原文、结论、每个测试点的状态与耗时。"""
    return reader.student(actor, assessment_id, student_pk)


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
    archived: bool = False,
    offset: Offset = 0,
    limit: Limit = 20,
) -> BatchList:
    return backend.list_batches(
        actor,
        data_scope_id=data_scope_id,
        include_revisions=include_revisions,
        archived=archived,
        offset=offset,
        limit=limit,
    )


@router.get("/batches/{batch_id}", response_model=Batch)
def batch(batch_id: str, actor: Actor, backend: Backend) -> Batch:
    return backend.get_batch(actor, batch_id)


@router.patch("/batches/{batch_id}/archive", response_model=BatchArchive)
def archive_batch(
    batch_id: str, body: BatchArchive, actor: Actor, backend: Backend
) -> BatchArchive:
    return backend.set_batch_archive(actor, batch_id, body)


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
    batch_id: str,
    student_id: str,
    actor: Actor,
    backend: Backend,
    snapshot_id: str | None = None,
) -> StudentEvidence:
    return BatchReader(backend.database, clock=backend.clock).student_evidence(
        actor, batch_id, student_id, snapshot_id=snapshot_id
    )


@router.get("/batches/{batch_id}/evidence/{evidence_id}", response_model=Evidence)
def evidence(
    batch_id: str,
    evidence_id: str,
    actor: Actor,
    backend: Backend,
    snapshot_id: str | None = None,
) -> Evidence:
    return BatchReader(backend.database, clock=backend.clock).evidence(
        actor, batch_id, evidence_id, snapshot_id=snapshot_id
    )


@router.get("/batches/{batch_id}/statistics", response_model=Statistics)
def statistics(
    batch_id: str,
    actor: Actor,
    backend: Backend,
    snapshot_id: str | None = None,
    x_dimension: DimensionCode | None = None,
    y_dimension: DimensionCode | None = None,
) -> Statistics:
    return AnalysisReader(backend.database, clock=backend.clock).statistics(
        actor,
        batch_id,
        snapshot_id=snapshot_id,
        x_dimension=x_dimension,
        y_dimension=y_dimension,
    )


@router.get("/batches/{batch_id}/summary", response_model=BatchSummary)
def batch_summary(
    batch_id: str,
    actor: Actor,
    backend: Backend,
    snapshot_id: str | None = None,
) -> BatchSummary:
    return AnalysisReader(backend.database, clock=backend.clock).summary(
        actor, batch_id, snapshot_id=snapshot_id
    )


@router.post(
    "/batches/{batch_id}/groups/query",
    response_model=GroupResult,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": group_schema()}},
        }
    },
)
async def group(
    batch_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
) -> GroupResult:
    # Authentication above precedes reading the bounded request body.
    query = await group_query(request)
    reader = AnalysisReader(backend.database, clock=backend.clock)
    return await run_in_threadpool(reader.group, actor, batch_id, query)


@router.get("/batches/{batch_id}/rubric", response_model=Rubric)
def rubric(batch_id: str, actor: Actor, backend: Backend, snapshot_id: str | None = None) -> Rubric:
    return BatchReader(backend.database, clock=backend.clock).rubric(
        actor, batch_id, snapshot_id=snapshot_id
    )


@router.post(
    "/batches/{batch_id}/insights/query",
    response_model=InsightResult,
    responses=INSIGHT_ERRORS,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": request_schema(InsightQuery)},
            },
        }
    },
)
async def insights(
    batch_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
) -> InsightResult:
    query = await read_query(request, InsightQuery)
    reader = InsightReader(backend.database, clock=backend.clock)
    return await run_in_threadpool(reader.query, actor, batch_id, query)


@router.post(
    "/batches/{batch_id}/insights/{candidate_id}/records/query",
    response_model=InsightRecordsResult,
    responses=INSIGHT_ERRORS,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": request_schema(InsightRecordsQuery)},
            },
        }
    },
)
async def insight_records(
    batch_id: str,
    candidate_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
) -> InsightRecordsResult:
    query = await read_query(request, InsightRecordsQuery)
    reader = InsightReader(backend.database, clock=backend.clock)
    return await run_in_threadpool(reader.records, actor, batch_id, candidate_id, query)


@router.get(
    "/batches/{batch_id}/capabilities", response_model=BatchCapabilities, responses=INSIGHT_ERRORS
)
def batch_capabilities(
    batch_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
    snapshot_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> BatchCapabilities:
    paths = request.app.openapi()["paths"]
    prefix = "/api/v1/edu/batches/{batch_id}"
    features: set[Action] = set()
    for action, suffix, method in (
        ("review_text", "/text-reviews/{kind}/{target_id}", "put"),
        ("save_analysis", "/analysis-results", "post"),
        ("edit_analysis", "/analysis-results/{result_id}", "patch"),
        ("export_analysis", "/analysis-results/{result_id}/export", "get"),
    ):
        if method in paths.get(prefix + suffix, {}):
            features.add(action)
    return DiagnosticReader(backend.database, clock=backend.clock).capabilities(
        actor,
        batch_id,
        snapshot_id=snapshot_id,
        available_features=features,
    )


@router.post(
    "/batches/{batch_id}/diagnostics/integrity",
    response_model=IntegrityReport,
    responses={**INSIGHT_ERRORS, 403: {"model": ErrorResponse}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": request_schema(IntegrityRequest)}},
        }
    },
)
async def batch_integrity(
    batch_id: str,
    request: Request,
    actor: Actor,
    backend: Backend,
) -> IntegrityReport:
    body = await read_query(request, IntegrityRequest)
    reader = DiagnosticReader(backend.database, clock=backend.clock)
    return await run_in_threadpool(reader.integrity, actor, batch_id, body)
