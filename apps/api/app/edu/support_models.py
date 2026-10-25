"""Export preflight and private, bounded request diagnostic DTOs."""

from typing import Annotated, Literal

from pydantic import Field

from .models import Identifier, Model


class ExportCheckRequest(Model):
    snapshot_id: Identifier
    expected_version: Annotated[int, Field(ge=1)]
    format: Literal["json", "zip"] = "zip"


class ExportWarning(Model):
    code: Literal[
        "PENDING_FINDINGS_EXCLUDED",
        "DISMISSED_FINDINGS_EXCLUDED",
        "WITHHELD_COMMENTS_EXCLUDED",
        "WITHHELD_RUBRIC_EXCLUDED",
    ]
    count: int
    message: str


class ExportCheck(Model):
    batch_id: str
    snapshot_id: str
    result_id: str
    result_version: int
    format: Literal["json", "zip"]
    exportable: Literal[True] = True
    member_count: int
    confirmed_finding_count: int
    json_payload_bytes: int
    max_json_payload_bytes: int
    warnings: tuple[ExportWarning, ...]
    note: str = "预检不生成文件或锁定状态；实际下载时仍重新校验权限、版本和文本发布状态。"


class RequestTrace(Model):
    request_id: str
    route_template: str
    method: str
    status_code: int
    error_code: str | None
    stage: Literal[
        "completed",
        "authentication",
        "authorization_or_resource",
        "conflict",
        "validation",
        "limit",
        "service",
        "server",
        "request",
    ]
    duration_ms: int
    created_at: str
    expires_at: str
