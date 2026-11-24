"""B-02-S1 资料识别的对外响应模型。"""

from typing import Literal

from app.edu.models import Model


class MaterialTable(Model):
    ref: str
    rows: int
    columns: int
    complete: bool
    blocked: bool


class MaterialField(Model):
    column: str
    meaning: str
    evidence: tuple[str, ...]


class MaterialPending(Model):
    code: str
    detail: str


class MaterialRecognition(Model):
    source_id: str
    file_name: str | None
    kind: str
    supported: bool
    role: str | None
    role_state: Literal["candidate", "needs_confirmation", "unsupported"]
    route: Literal["material_understanding", "assessment_import", "unsupported"]
    tables: tuple[MaterialTable, ...]
    fields: tuple[MaterialField, ...]
    pending: tuple[MaterialPending, ...]
    analyses: tuple[str, ...]
