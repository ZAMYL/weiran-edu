"""B-02-S2 read contract: facts derived from confirmed positions, never invented."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import Page

FactKind = Literal["score", "dimension"]
IdentityBasis = Literal["student_number", "none"]


class MaterialFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    material_id: str
    kind: FactKind
    participant_key: str
    student_id: str | None = None
    identity_basis: IdentityBasis
    problem_key: str | None = None
    dimension: str | None = None
    score: str | None = None
    score_value: float | None = None
    verdict: str | None = None
    dimension_score: str | None = None
    dimension_score_value: float | None = None
    feedback: str | None = None
    source_table: str
    source_row: int
    created_at: str


class MaterialFactList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    source_id: str
    plan_sha256: str | None = None
    rule_version: str
    total_count: int
    items: list[MaterialFact] = Field(default_factory=list)
    page: Page


class MaterialFactsIngest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    source_id: str
    plan_sha256: str | None = None
    file_sha256: str
    rows_scanned: int
    facts_written: int
    linked_count: int
    skipped_without_participant: int


class MaterialProblemStatistic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_key: str | None = None
    fact_count: int
    scored_count: int
    mean: str
    minimum: str
    maximum: str
    verdicts: dict[str, int] = Field(default_factory=dict)


class MaterialDimensionStatistic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    fact_count: int
    scored_count: int
    mean: str
    minimum: str
    maximum: str


class MaterialFactStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    source_id: str
    plan_sha256: str | None = None
    rule_version: str
    fact_count: int
    participant_count: int
    linked_participant_count: int
    problems: list[MaterialProblemStatistic] = Field(default_factory=list)
    dimensions: list[MaterialDimensionStatistic] = Field(default_factory=list)
