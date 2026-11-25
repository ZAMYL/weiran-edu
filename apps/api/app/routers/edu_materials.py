"""B-02-S1: one upload entry; the backend decides roles, never the caller.

The DS-04/S1 material stack already turns bytes into a locally validated plan.
This module only adds HTTP framing: bounded multipart reading, a server
generated source ID, and a DTO that cannot claim a confirmation. Persisted
records keep the structure snapshot and teacher confirmations, never raw bytes.
"""

import hashlib
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.ai.edu.material_contract import MaterialInput, MaterialPlan
from app.ai.edu.material_semantics import extract_material_input, understand_materials
from app.core.errors import APIError
from app.core.exact_json import ExactJSONResponse
from app.edu.auth import Principal, authenticate, deny
from app.edu.material_fact_models import (
    MaterialFactList,
    MaterialFactsIngest,
    MaterialFactStatistics,
)
from app.edu.material_facts import MaterialFactStore
from app.edu.material_models import (
    MaterialArchive,
    MaterialCapability,
    MaterialColumn,
    MaterialConfirmRequest,
    MaterialField,
    MaterialFile,
    MaterialRecognition,
    MaterialRecordList,
    MaterialRecordView,
    MaterialRole,
    MaterialTable,
)
from app.edu.materials import MaterialIntake
from app.importing.edu.detect import ZIP_HTML, ZIP_OTHER, detect_asset
from app.importing.edu.types import ImportFileError, ParseLimits

router = APIRouter(prefix="/api/v1/edu", tags=["EDU"], default_response_class=ExactJSONResponse)
bearer = HTTPBearer(auto_error=False)
MAX_MATERIAL_FILES = 10
ARCHIVE_ROUTES = {
    ZIP_HTML: (
        "/api/v1/edu/assessments",
        "含网页答卷的压缩包，交由既有答题卡导入路径处理；通用入口不重复解析",
    ),
    ZIP_OTHER: (
        "/api/v1/edu/imports/preview",
        "压缩包不在通用资料识别范围，交由既有预览导入路径处理",
    ),
}


def _principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        deny(401, "UNAUTHORIZED")
    actor = authenticate(request.app.state.edu_service.database, credentials.credentials)
    request.state.trace_teacher_id = actor.teacher_id
    return actor


def intake_of(request: Request) -> MaterialIntake:
    service = request.app.state.edu_service
    return MaterialIntake(service.database, clock=service.clock)


def facts_of(request: Request) -> MaterialFactStore:
    service = request.app.state.edu_service
    return MaterialFactStore(service.database, clock=service.clock)


type Actor = Annotated[Principal, Depends(_principal)]
type Intake = Annotated[MaterialIntake, Depends(intake_of)]
type Facts = Annotated[MaterialFactStore, Depends(facts_of)]


async def _upload_files(request: Request) -> list[tuple[bytes, str | None, str | None]]:
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
        async with limited.form(
            max_files=MAX_MATERIAL_FILES, max_fields=1, max_part_size=4096
        ) as form:
            items = list(form.multi_items())
            if set(key for key, _ in items) - {"file"}:
                raise APIError(422, "VALIDATION_ERROR", "上传字段不符合要求")
            files = [
                value for key, value in items if key == "file" and isinstance(value, UploadFile)
            ]
            if not files:
                raise APIError(422, "VALIDATION_ERROR", "缺少上传文件")
            if len(files) > MAX_MATERIAL_FILES:
                raise APIError(422, "VALIDATION_ERROR", "一次上传的文件数量超过限制")
            parts: list[tuple[bytes, str | None, str | None]] = []
            total = 0
            for upload in files:
                data = await upload.read(ParseLimits().compressed_bytes + 1)
                total += len(data)
                if (
                    len(data) > ParseLimits().compressed_bytes
                    or total > ParseLimits().compressed_bytes
                ):
                    raise APIError(413, "IMPORT_FILE_TOO_LARGE", "上传文件超过限制")
                parts.append((data, upload.filename, upload.content_type))
            return parts
    except APIError:
        raise
    except Exception:
        raise APIError(422, "IMPORT_FILE_INVALID", "上传表单无法读取") from None


def _table_views(plan: MaterialPlan, file_ref: str) -> list[MaterialTable]:
    views = []
    for table in plan.overview.get("tables", []):
        if table.get("file") != file_ref:
            continue
        columns = []
        for column in table.get("columns", []):
            locator = plan.locators.get(column["ref"])
            columns.append(
                MaterialColumn(
                    ref=column["ref"],
                    header=locator.column_name if locator is not None else None,
                    present=column["present"],
                    types=list(column.get("types", [])),
                    blocked=bool(column.get("blocked")),
                )
            )
        views.append(
            MaterialTable(
                ref=table["ref"],
                name=plan.locators[table["ref"]].table_name
                if table["ref"] in plan.locators
                else None,
                rows=table["observed_rows"],
                columns=columns,
            )
        )
    return views


def _roles(plan: MaterialPlan, source_id: str) -> list[MaterialRole]:
    roles = []
    for (ref, role), verdict in sorted(plan.assessment.roles.items()):
        locator = plan.locators.get(ref)
        if locator is None or locator.source_id != source_id or locator.table_index is None:
            continue
        roles.append(
            MaterialRole(table=ref, role=role, state=verdict.state, reasons=list(verdict.reasons))
        )
    return roles


def _fields(plan: MaterialPlan, source_id: str) -> list[MaterialField]:
    fields = []
    for (ref, meaning), verdict in sorted(plan.assessment.fields.items()):
        locator = plan.locators.get(ref)
        if locator is None or locator.source_id != source_id or locator.column_index is None:
            continue
        fields.append(
            MaterialField(
                column=ref,
                header=locator.column_name,
                meaning=meaning,
                state=verdict.state,
                reasons=list(verdict.reasons),
            )
        )
    return fields


def _capabilities(plan: MaterialPlan, table_refs: set[str]) -> list[MaterialCapability]:
    return [
        MaterialCapability(
            name=cap.name,
            module=cap.module,
            table=cap.table,
            available=cap.available,
            missing=list(cap.missing),
            needs_confirmation=cap.needs_confirmation,
        )
        for cap in plan.capabilities
        if cap.table in table_refs
    ]


def _pending(plan: MaterialPlan, source_id: str) -> list[str]:
    return [
        ref
        for ref in plan.needs_confirmation
        if (locator := plan.locators.get(ref)) is not None and locator.source_id == source_id
    ]


def _file_view(
    plan: MaterialPlan,
    material: MaterialInput,
    *,
    file_ref: str,
    file_name: str,
    byte_size: int,
) -> MaterialFile:
    tables = _table_views(plan, file_ref)
    table_refs = {table.ref for table in tables}
    return MaterialFile(
        file_name=file_name,
        kind=material.document.kind,
        byte_size=byte_size,
        source_id=material.source_id,
        status="recognized",
        tables=tables,
        roles=_roles(plan, material.source_id),
        fields=_fields(plan, material.source_id),
        pending=_pending(plan, material.source_id),
        analyses=_capabilities(plan, table_refs),
        warnings=list(material.document.warnings),
    )


@router.post("/materials", response_model=MaterialRecognition)
async def recognize_materials(
    request: Request, actor: Actor, intake: Intake
) -> MaterialRecognition:
    """Upload one or more supported files; return what the backend actually recognized.

    Nothing is confirmed here: the server computes the source ID, stores only the
    structure snapshot, and unresolved positions come back as pending confirmations.
    """
    parts = await _upload_files(request)
    archives: list[MaterialArchive] = []
    materials: list[tuple[MaterialInput, str, int, str]] = []
    unsupported: list[MaterialFile] = []
    seen_sources: dict[str, int] = {}
    for data, name, content_type in parts:
        digest = hashlib.sha256(data).hexdigest()
        occurrence = seen_sources.get(digest, 0) + 1
        seen_sources[digest] = occurrence
        source_id = f"sha256:{digest}" if occurrence == 1 else f"sha256:{digest}#{occurrence}"
        detected = detect_asset(data, content_type=content_type)
        if detected.kind in ARCHIVE_ROUTES:
            route, note = ARCHIVE_ROUTES[detected.kind]
            archives.append(
                MaterialArchive(
                    file_name=name or "unnamed",
                    kind=detected.kind,
                    route=route,
                    note=note,
                )
            )
            continue
        try:
            material = await run_in_threadpool(
                extract_material_input,
                data,
                source_id=source_id,
                content_type=content_type,
            )
        except ImportFileError as exc:
            unsupported.append(
                MaterialFile(
                    file_name=name or "unnamed",
                    kind=detected.kind,
                    byte_size=len(data),
                    source_id=source_id,
                    status="unsupported",
                    error=exc.code,
                )
            )
            continue
        materials.append((material, name or "unnamed", len(data), digest))
    recognition_id = (
        "sha256:"
        + hashlib.sha256(
            "".join(material.source_id for material, _, _, _ in materials).encode() or b"archives"
        ).hexdigest()
    )
    if not materials:
        return MaterialRecognition(
            recognition_id=recognition_id, materials=unsupported, archives=archives
        )
    try:
        plan = await understand_materials(tuple(material for material, _, _, _ in materials))
    except ValueError as exc:
        raise APIError(422, "MATERIAL_PLAN_UNSUPPORTED", "资料超出可识别范围") from exc
    views: list[MaterialFile] = []
    first_by_digest: dict[str, MaterialFile] = {}
    for index, (material, name, size, digest) in enumerate(materials, 1):
        view = _file_view(plan, material, file_ref=f"F{index}", file_name=name, byte_size=size)
        first = first_by_digest.get(digest)
        if first is None:
            first_by_digest[digest] = view
        else:
            # 同一份字节只对应库里的首发记录；副本沿用首发的 ref 与 source_id，
            # 否则它的 F2.* 定位在已存快照里根本不存在。
            view = first.model_copy(update={"file_name": view.file_name})
        views.append(view)
    stored = await run_in_threadpool(
        intake.record,
        actor,
        [(digest, view) for (_, _, _, digest), view in zip(materials, views, strict=True)],
    )
    # 同一份字节在同一请求里出现多次时，库里只登记首发那条记录；
    # 让后续副本也报告首发 source_id，否则它的 `#n` 别名无法确认或回读。
    views = [
        view.model_copy(update={"source_id": canonical})
        for view, (_, canonical) in zip(views, stored, strict=True)
    ]
    return MaterialRecognition(
        recognition_id=recognition_id,
        materials=views + unsupported,
        archives=archives,
        pending=sorted({ref for view in views for ref in view.pending}),
    )


@router.post("/materials/confirm", response_model=MaterialRecordView)
async def confirm_materials(
    body: MaterialConfirmRequest, actor: Actor, intake: Intake
) -> MaterialRecordView:
    """Record a teacher's explicit confirmation; the only source of `confirmed`."""
    return await run_in_threadpool(intake.confirm, actor, body)


@router.get("/materials", response_model=MaterialRecordList)
async def list_materials(
    actor: Actor,
    intake: Intake,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    status: Annotated[Literal["active", "revoked"] | None, Query()] = None,
) -> MaterialRecordList:
    """List the caller's stored records, newest first; never another teacher's."""
    items, total = await run_in_threadpool(
        intake.list, actor, offset=offset, limit=limit, status=status
    )
    return MaterialRecordList(items=items, total_count=total)


@router.get("/materials/{source_id}", response_model=MaterialRecordView)
async def read_material(source_id: str, actor: Actor, intake: Intake) -> MaterialRecordView:
    """Read the stored snapshot plus confirmations; never re-parses or re-uploads."""
    return await run_in_threadpool(intake.read, actor, source_id)


@router.post("/materials/{source_id}/facts", response_model=MaterialFactsIngest)
async def ingest_material_facts(
    source_id: str, request: Request, actor: Actor, store: Facts
) -> MaterialFactsIngest:
    """Re-upload the exact same bytes to turn confirmed positions into facts.

    The stored record only keeps a structure snapshot, so the cells have to come from
    the file itself. A different byte sequence can never inherit a confirmation.
    """
    parts = await _upload_files(request)
    if len(parts) != 1:
        raise APIError(422, "VALIDATION_ERROR", "写入业务事实需要且只允许一份文件")
    data, name, content_type = parts[0]
    digest = hashlib.sha256(data).hexdigest()
    await run_in_threadpool(store.check_bytes, actor, source_id, digest)
    detected = detect_asset(data, content_type=content_type)
    if detected.kind in ARCHIVE_ROUTES:
        raise APIError(
            422,
            "MATERIAL_FACTS_ARCHIVE_UNSUPPORTED",
            "压缩包按既有答题卡导入路径处理，不在业务事实写入范围",
        )
    try:
        document = await run_in_threadpool(
            extract_material_input, data, source_id=source_id, content_type=content_type
        )
    except ImportFileError as exc:
        raise APIError(422, "MATERIAL_FACTS_UNSUPPORTED", "该文件无法按已登记结构解析") from exc
    plan = await understand_materials((document,))
    return await run_in_threadpool(store.ingest, actor, source_id, digest, document, plan)


@router.get("/materials/{source_id}/facts", response_model=MaterialFactList)
async def list_material_facts(
    source_id: str,
    actor: Actor,
    store: Facts,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> MaterialFactList:
    return await run_in_threadpool(store.list, actor, source_id, offset=offset, limit=limit)


@router.get("/materials/{source_id}/facts/statistics", response_model=MaterialFactStatistics)
async def material_fact_statistics(
    source_id: str, actor: Actor, store: Facts
) -> MaterialFactStatistics:
    return await run_in_threadpool(store.statistics, actor, source_id)
