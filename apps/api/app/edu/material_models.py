"""B-02-S1 response contract: recognized material, never a confirmation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["confirmed", "candidate", "needs_confirmation", "unsupported"]


class MaterialColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    header: str | None
    present: int
    types: list[str] = Field(default_factory=list)
    blocked: bool = False


class MaterialTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    name: str | None
    rows: int
    columns: list[MaterialColumn]


class MaterialRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    role: str
    state: State
    reasons: list[str] = Field(default_factory=list)


class MaterialField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    header: str | None
    meaning: str
    state: State
    reasons: list[str] = Field(default_factory=list)


class MaterialCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    module: str
    table: str | None
    available: bool
    missing: list[str] = Field(default_factory=list)
    needs_confirmation: bool


class MaterialFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    kind: str
    byte_size: int
    source_id: str
    status: Literal["recognized", "unsupported"]
    tables: list[MaterialTable] = Field(default_factory=list)
    roles: list[MaterialRole] = Field(default_factory=list)
    fields: list[MaterialField] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    analyses: list[MaterialCapability] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class MaterialArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    kind: str
    route: str
    note: str


class MaterialRecognition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recognition_id: str
    coverage: Literal["unknown"] = "unknown"
    authority: Literal["none"] = "none"
    materials: list[MaterialFile] = Field(default_factory=list)
    archives: list[MaterialArchive] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)


class MaterialDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["role", "field"]
    table: str
    column: str | None = None
    value: str


class MaterialConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=8, max_length=96)
    data_scope_id: str = Field(min_length=1, max_length=128)
    decisions: list[MaterialDecision] = Field(min_length=1, max_length=240)


class MaterialConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: str
    kind: Literal["role", "field"]
    table: str
    column: str | None = None
    value: str
    confirmed_by: str
    confirmed_at: str
    basis: str


class MaterialRecordView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    source_id: str
    status: Literal["active", "revoked"]
    data_scope_id: str | None
    created_at: str
    file: MaterialFile
    confirmed: list[MaterialDecision] = Field(default_factory=list)
    confirmations: list[MaterialConfirmation] = Field(default_factory=list)


class MaterialRecordList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MaterialRecordView] = Field(default_factory=list)
    total_count: int
