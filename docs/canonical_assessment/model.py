"""Internal design objects, not HTTP DTOs, storage models or an import parser."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from re import fullmatch
from typing import Literal

PROFILE = {
    "algorithm_data_structure": "算法与数据结构",
    "code_implementation": "代码实现能力",
    "problem_modeling": "问题建模能力",
    "efficiency_awareness": "效率意识",
    "solution_strategy": "解题策略",
}
type Purpose = Literal["teacher_analysis", "display", "external_model"]
type EvidenceKind = Literal["score", "comment", "response", "criteria"]


def exact(raw: str) -> Fraction:
    """Source adapters supply decimal lexemes, never binary floats or booleans."""
    if (
        not isinstance(raw, str)
        or len(raw) > 64
        or not fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", raw)
    ):
        raise ValueError("invalid_numeric_lexeme")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError("invalid_numeric_lexeme") from error
    if not value.is_finite() or abs(value.adjusted()) > 100:
        raise ValueError("nonfinite_or_oversized_number")
    return Fraction(value)


@dataclass(frozen=True)
class Scale:
    scale_id: str
    minimum: str
    maximum: str
    step: str
    unit: str

    def __post_init__(self) -> None:
        if exact(self.minimum) >= exact(self.maximum) or exact(self.step) <= 0:
            raise ValueError("invalid_scale")


@dataclass(frozen=True)
class Batch:
    batch_id: str
    version: str
    source_ids: tuple[str, ...]
    assessment_date: str | None = None


@dataclass(frozen=True)
class Participant:
    participant_id: str
    scope_id: str


@dataclass(frozen=True)
class AssessmentItem:
    item_id: str
    name: str
    kind: Literal["competency", "question", "task"]
    scale: Scale | None
    rubric_id: str | None = None
    confirmed_profile_code: str | None = None


@dataclass(frozen=True)
class Rubric:
    rubric_id: str
    version: str
    item_ids: tuple[str, ...]
    criteria_evidence_id: str


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_id: str
    kind: EvidenceKind
    item_id: str
    participant_id: str | None
    permitted_purposes: tuple[Purpose, ...] = ()
    provenance: Literal["user_confirmed", "retrieved", "assumption", "unknown"] = "unknown"


@dataclass(frozen=True)
class Observation:
    observation_id: str
    participant_id: str
    item_id: str
    raw_decimal: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    version: str
    batch: Batch
    participants: tuple[Participant, ...]
    items: tuple[AssessmentItem, ...]
    observations: tuple[Observation, ...]
    evidence: tuple[Evidence, ...]
    rubrics: tuple[Rubric, ...] = ()
    history_batch_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        """Fail closed on structural ambiguity; never silently drop facts."""
        for rows, field in (
            (self.participants, "participant_id"),
            (self.items, "item_id"),
            (self.observations, "observation_id"),
            (self.evidence, "evidence_id"),
            (self.rubrics, "rubric_id"),
        ):
            ids = [getattr(row, field) for row in rows]
            if any(not value or value != value.strip() for value in ids) or len(ids) != len(
                set(ids)
            ):
                raise ValueError("invalid_or_duplicate_id")
        participants = {p.participant_id for p in self.participants}
        items = {i.item_id: i for i in self.items}
        evidence = {e.evidence_id: e for e in self.evidence}
        rubrics = {r.rubric_id: r for r in self.rubrics}
        seen = set()
        for item in self.items:
            if item.kind not in ("competency", "question", "task"):
                raise ValueError("unknown_item_kind")
            if item.confirmed_profile_code is not None and (
                item.confirmed_profile_code not in PROFILE or item.kind != "competency"
            ):
                raise ValueError("invalid_confirmed_profile_mapping")
            if item.rubric_id is not None and (
                item.rubric_id not in rubrics
                or item.item_id not in rubrics[item.rubric_id].item_ids
            ):
                raise ValueError("rubric_scope_mismatch")
        for ref in self.evidence:
            if (
                ref.item_id not in items
                or ref.source_id not in self.batch.source_ids
                or ref.kind not in ("score", "comment", "response", "criteria")
                or ref.participant_id is not None
                and ref.participant_id not in participants
                or any(
                    p not in ("teacher_analysis", "display", "external_model")
                    for p in ref.permitted_purposes
                )
            ):
                raise ValueError("invalid_evidence_scope")
        for rubric in self.rubrics:
            ref = evidence.get(rubric.criteria_evidence_id)
            if (
                not rubric.item_ids
                or any(item_id not in items for item_id in rubric.item_ids)
                or ref is None
                or ref.kind != "criteria"
                or ref.participant_id is not None
                or ref.item_id not in rubric.item_ids
            ):
                raise ValueError("invalid_rubric_evidence")
        for observation in self.observations:
            key = (observation.participant_id, observation.item_id)
            if key in seen or key[0] not in participants or key[1] not in items:
                raise ValueError("duplicate_or_unknown_observation_target")
            seen.add(key)
            for evidence_id in observation.evidence_ids:
                ref = evidence.get(evidence_id)
                if ref is None or (ref.participant_id, ref.item_id) != key:
                    raise ValueError("observation_evidence_mismatch")
