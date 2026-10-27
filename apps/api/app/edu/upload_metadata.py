"""Defaults for an existing-format upload; never infer scoring or authorization from a file."""

from unicodedata import category

from app.core.errors import APIError
from app.storage.database import Database

from .auth import Principal, accessible_batch, deny, require_scope, require_teacher
from .models import Identifier, ImportMetadata


class UploadMetadata(ImportMetadata):
    # Internal multipart model. Existing service callers still supply complete ImportMetadata.
    data_scope_id: Identifier | None = None


def filename_title(filename: str) -> str:
    """Use a bounded basename as a display default, never a path or a format declaration."""
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = basename[:-5] if basename.lower().endswith(".xlsx") else basename
    cleaned = "".join(char for char in stem if not category(char).startswith("C"))
    return cleaned.strip()[:100].strip() or "未命名测评"


def resolve_upload_metadata(
    database: Database,
    principal: Principal,
    metadata: UploadMetadata,
    *,
    now: str,
) -> ImportMetadata:
    """Choose only an unambiguous authorized scope; create_import rechecks it at each gate."""
    with database.transaction() as connection:
        require_teacher(connection, principal, now)
        scope_id = metadata.data_scope_id
        if scope_id is None and metadata.revision_of_batch_id is not None:
            batch = accessible_batch(connection, principal, metadata.revision_of_batch_id)
            if batch["owner_teacher_id"] != principal.teacher_id:
                deny(404)
            scope_id = batch["data_scope_id"]
        if scope_id is None:
            rows = connection.execute(
                "SELECT data_scope_id FROM scope_members "
                "WHERE teacher_id=? AND can_import=1 LIMIT 2",
                (principal.teacher_id,),
            ).fetchall()
            if not rows:
                deny(403, "FORBIDDEN")
            if len(rows) != 1:
                raise APIError(
                    409,
                    "IMPORT_SCOPE_REQUIRED",
                    "无法确定测评归属，请从当前教学空间上传文件",
                )
            scope_id = rows[0]["data_scope_id"]
        require_scope(connection, principal, scope_id, importing=True)
    return ImportMetadata(**metadata.model_dump(exclude={"data_scope_id"}), data_scope_id=scope_id)
