"""Current-authority database snapshot, not SyntheticProvider or a recipe substitution."""

import json
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Literal

from docs.canonical_assessment.model import (
    AssessmentItem,
    Batch,
    Dataset,
    Evidence,
    Observation,
    Participant,
    Scale,
)
from prototypes.adaptive_assessment.data import Envelope, Materialized, Witness
from pydantic import Field

from app.ai.edu.discovery.models import Strict
from app.core.errors import APIError
from app.edu.analysis import DIMENSION_MAP, AnalysisReader
from app.edu.auth import Principal
from app.edu.reviews import require_owner
from app.importing.edu import parse_excel
from app.importing.edu.types import DIMENSIONS, ImportFileError

ALIASES = {f"I{i + 1:02}": spec.code for i, spec in enumerate(DIMENSIONS)}
REVERSE = {v: k for k, v in ALIASES.items()}


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class ValidationState:
    """Mutable state owned by one request-local DatabaseSource only."""

    depth: int = 0


@dataclass(frozen=True)
class DatabaseEnvelope(Envelope):
    provider: object = field(repr=False)
    actor: Principal = field(repr=False)
    batch_id: str
    bound_snapshot: str
    cancelled: object = field(default=None, repr=False)
    validation_state: ValidationState = field(
        default_factory=ValidationState, repr=False, compare=False
    )

    @property
    def snapshot_id(self):
        return sha256(self.source_bytes).hexdigest()

    def validate(self):
        if self.cancelled is not None and self.cancelled():
            raise APIError(499, "AI_CANCELLED", "调查请求已取消")
        self.data.validate()
        # A validation scope has already captured the complete immutable snapshot.
        # Nested checks still execute cancellation/authorization and in-memory data
        # validation; the outer scope performs a fresh whole-source check on exit.
        if self.validation_state.depth:
            return
        current, manifest = self.provider.capture(self.actor, self.batch_id, self.bound_snapshot)
        if current != self.material or manifest != self.source_bytes:
            raise APIError(503, "DATA_INTEGRITY_ERROR", "调查快照或来源已变化")


@dataclass(frozen=True)
class DatabaseSource:
    recipe: str  # Budget binding key, NOT a registered synthetic recipe.
    envelope: DatabaseEnvelope = field(repr=False)
    fingerprint: str

    def validate(self):
        if self.fingerprint != sha256(self.envelope.source_bytes).hexdigest():
            raise APIError(503, "DATA_INTEGRITY_ERROR", "调查快照不一致")
        self.envelope.validate()

    @contextmanager
    def validation_scope(self, *, prevalidated: bool = False):
        """Reuse a verified snapshot only inside one fail-closed validation phase.

        `prevalidated=True` is for a caller that has just materialized and compared
        this exact source in the same request; the phase still re-checks the whole
        source on exit, so a change during the phase is never returned as verified.
        """

        state = self.envelope.validation_state
        outermost = state.depth == 0
        if not prevalidated:
            self.validate()
        state.depth += 1
        try:
            yield self
        finally:
            state.depth -= 1
            if outermost:
                # Detect source changes during the phase before any caller returns.
                self.validate()


REAL_DATA_APPROVAL_MAX_BYTES = 65_536
REPOSITORY = Path(__file__).resolve().parents[2]


class RealDataSource(Strict):
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    valid_until: str
    human_approval_reference: str = Field(
        min_length=8, max_length=120, pattern=r"^[A-Za-z0-9:_-]+$"
    )

    def check(self, today: date) -> None:
        try:
            expiry = date.fromisoformat(self.valid_until)
        except ValueError:
            raise APIError(403, "AI_REAL_DATA_APPROVAL_STALE", "真实数据授权日期无效") from None
        if expiry < today:
            raise APIError(403, "AI_REAL_DATA_APPROVAL_STALE", "真实数据授权已过期")


class RealDataAdmission(Strict):
    """Operator-owned allowlist for real teacher batches. No browser can create one."""

    purpose: Literal["real_teacher_data"]
    human_approval_reference: str = Field(
        min_length=8, max_length=120, pattern=r"^[A-Za-z0-9:_-]+$"
    )
    independent_review_reference: str = Field(min_length=8, max_length=200)
    reviewed_code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    valid_until: str
    sources: tuple[RealDataSource, ...] = Field(min_length=1, max_length=100)

    @classmethod
    def load(
        cls,
        path: Path | str,
        *,
        today: date | None = None,
        code_sha256: str | None = None,
    ) -> "RealDataAdmission":
        path = Path(path).expanduser()
        if not path.is_absolute() or path.resolve().is_relative_to(REPOSITORY):
            raise APIError(403, "AI_REAL_DATA_APPROVAL_LOCATION", "真实数据授权文件必须在仓库外")
        try:
            payload = path.read_bytes()
        except OSError:
            raise APIError(403, "AI_REAL_DATA_APPROVAL_MISSING", "真实数据授权文件不可读") from None
        if len(payload) > REAL_DATA_APPROVAL_MAX_BYTES:
            raise APIError(413, "AI_REAL_DATA_APPROVAL_TOO_LARGE", "真实数据授权文件过大")
        admission = cls.model_validate_json(payload)
        admission.check(today=today)
        if code_sha256 is not None and admission.reviewed_code_sha256 != code_sha256:
            raise APIError(403, "AI_REAL_DATA_REVIEW_STALE", "真实数据审核绑定的代码已变化")
        return admission

    def check(self, *, today: date | None = None) -> None:
        current = today or date.today()
        try:
            expiry = date.fromisoformat(self.valid_until)
        except ValueError:
            raise APIError(403, "AI_REAL_DATA_APPROVAL_STALE", "真实数据授权日期无效") from None
        if expiry < current:
            raise APIError(403, "AI_REAL_DATA_APPROVAL_STALE", "真实数据授权已过期")
        for source in self.sources:
            source.check(current)

    def require(self, source_sha256: str, *, today: date | None = None) -> None:
        current = today or date.today()
        self.check(today=current)
        matching = [source for source in self.sources if source.source_sha256 == source_sha256]
        if not matching:
            raise APIError(403, "AI_REAL_DATA_NOT_APPROVED", "这份真实测评尚未获得外发批准")
        for source in matching:
            source.check(current)


class DatabaseProvider:
    """Local test host only. Real teacher auth; only server-generated XLSX hashes allowed.

    A consumed tuple cannot be reset by another HTTP request in this host. This is not
    durable cross-process disclosure history: production registration is forbidden.
    """

    def __init__(
        self,
        service,
        generated_workbooks: tuple[bytes, ...],
        *,
        allow_unregistered: bool = False,
    ):
        if type(generated_workbooks) is not tuple:
            raise ValueError("server_generated_sources_required")
        if not generated_workbooks and not allow_unregistered:
            raise ValueError("server_generated_sources_required")
        self._scoring = {}
        self._allow_unregistered = allow_unregistered
        for raw in generated_workbooks:
            if type(raw) is not bytes:
                raise ValueError("server_generated_bytes_required")
            parsed = parse_excel(raw)
            if not parsed.can_commit:
                raise ValueError("invalid_generated_fixture")
            self._scoring[parsed.file_sha256] = self._score_cells(parsed)
        self.service = service
        self.reader = AnalysisReader(service.database, clock=service.clock)
        self.allowed_hashes = frozenset(self._scoring)
        self._consumed = set()
        self._lock = Lock()

    @staticmethod
    def _score_cells(parsed) -> dict:
        """Retain only original score coordinates/lexemes, never names or comments."""
        cells = {}
        for index, block in enumerate(parsed.students):
            for evaluation in block.evaluations:
                origin = evaluation.source
                key = (
                    origin.sheet_name,
                    origin.row_number,
                    evaluation.dimension_code,
                    origin.score_cell,
                    origin.dimension_cell,
                    origin.comment_cell,
                )
                if key in cells:
                    raise ValueError("ambiguous_generated_witness")
                cells[key] = (index, evaluation.score_units, str(evaluation.score_raw))
        return cells

    def _scores_for(self, file_sha256: str, raw: bytes) -> dict:
        cached = self._scoring.get(file_sha256)
        if cached is not None:
            return cached
        if not self._allow_unregistered:
            raise APIError(403, "AI_SYNTHETIC_SOURCE_REQUIRED", "当前仅开放服务器登记的合成测评")
        try:
            parsed = parse_excel(raw)
        except (ImportFileError, ValueError):
            raise APIError(422, "IMPORT_FILE_INVALID", "真实测评原件无法安全解析") from None
        if not parsed.can_commit or parsed.file_sha256 != file_sha256:
            raise APIError(422, "IMPORT_FILE_INVALID", "真实测评原件不一致")
        cells = self._score_cells(parsed)
        self._scoring[file_sha256] = cells
        return cells

    def require_registered_source(self, file_sha256: str) -> None:
        if file_sha256 not in self.allowed_hashes:
            raise APIError(403, "AI_SYNTHETIC_SOURCE_REQUIRED", "当前仅开放服务器登记的合成测评")

    def capture(self, actor: Principal, batch_id: str, snapshot: str) -> tuple[Materialized, bytes]:
        with self.service.database.transaction() as connection:
            batch = self.reader._batch(connection, actor, batch_id, snapshot)
            require_owner(batch, actor)
            self.require_registered_source(batch["file_sha256"])
            original = connection.execute(
                "SELECT raw_xlsx FROM batch_sources WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if original is None or sha256(original["raw_xlsx"]).hexdigest() != batch["file_sha256"]:
                raise APIError(503, "DATA_INTEGRITY_ERROR", "测评原文件摘要不一致")
            original_scores = self._scores_for(batch["file_sha256"], original["raw_xlsx"])
            dataset, students = self.reader._load(connection, batch)
            if not 5 <= len(students) <= 100:
                raise APIError(422, "AI_SAMPLE_LIMIT", "AI 接入验证仅接受5至100份记录")
            proofs = {
                row["evidence_id"]: self.reader._evidence(row)
                for row in connection.execute(self.reader._evidence_rows(), (batch_id,))
            }
            evidence, observations, witnesses, manifest = [], [], [], []
            # prepare_dataset sorts by anonymous ID; source row order is the formal
            # batch_students display order, never the dataset tuple's incidental order.
            original_order = {student.student_id: index for index, student in enumerate(students)}
            for record in dataset.records:
                ref = record.reference
                position = original_order[ref.student_id]
                for source in ref.evidence_refs:
                    proof = proofs.get(source.location)
                    code = next(k for k, dim in DIMENSION_MAP.items() if dim == source.dimension)
                    item = REVERSE[code]
                    score = record.score(source.dimension).value
                    if proof is None or (
                        proof.batch_id,
                        proof.snapshot_id,
                        proof.student_id,
                        proof.dimension_code,
                        proof.score,
                    ) != (batch_id, snapshot, ref.student_id, code, float(score)):
                        raise APIError(503, "DATA_INTEGRITY_ERROR", "调查评分依据不一致")
                    if (
                        source.source_id != batch["file_sha256"]
                        or proof.source.file_sha256 != source.source_id
                    ):
                        raise APIError(503, "DATA_INTEGRITY_ERROR", "调查来源不一致")
                    units = score * 100
                    if units.denominator != 1:
                        raise APIError(503, "DATA_INTEGRITY_ERROR", "评分精度不一致")
                    raw = f"{int(units) // 100}.{int(units) % 100:02d}"
                    original_key = (
                        proof.source.sheet_name,
                        proof.source.row_number,
                        code,
                        proof.source.score_cell,
                        proof.source.dimension_cell,
                        proof.source.comment_cell,
                    )
                    if original_scores.get(original_key) != (position, int(units), proof.score_raw):
                        raise APIError(
                            503, "DATA_INTEGRITY_ERROR", "评分与原合成文件坐标内容不一致"
                        )
                    locator = proof.source.sheet_name + "!" + proof.source.score_cell
                    evidence.append(
                        Evidence(
                            source.location,
                            source.source_id,
                            "score",
                            item,
                            ref.student_id,
                            ("teacher_analysis",),
                            "retrieved",
                        )
                    )
                    observations.append(
                        Observation(
                            "o-" + source.location, ref.student_id, item, raw, (source.location,)
                        )
                    )
                    for ident, kind in (
                        (source.location, "score"),
                        ("row-" + ref.student_id + "-" + item, "row"),
                    ):
                        witnesses.append(
                            Witness(
                                ident,
                                source.source_id,
                                snapshot,
                                locator,
                                source.source_id,
                                item,
                                ref.student_id,
                                kind,
                                raw,
                                sha256(raw.encode()).hexdigest(),
                            )
                        )
                    manifest.append(
                        (ref.student_id, item, raw, source.location, source.source_id, locator)
                    )
            material = Materialized(
                Dataset(
                    "authorized-db-" + batch_id,
                    snapshot,
                    Batch(batch_id, snapshot, (batch["file_sha256"],)),
                    tuple(Participant(s.student_id, batch["data_scope_id"]) for s in students),
                    tuple(
                        AssessmentItem(
                            alias,
                            spec.label,
                            "competency",
                            Scale("v1-" + alias, "1", "5", "0.01", "level"),
                            confirmed_profile_code=spec.code,
                        )
                        for alias, spec in zip(ALIASES, DIMENSIONS, strict=True)
                    ),
                    tuple(observations),
                    tuple(evidence),
                    (),
                ),
                tuple(witnesses),
                tuple((item, "ordinal") for item in ALIASES),
            )
            material.data.validate()
            raw = json.dumps(
                {
                    "batch_id": batch_id,
                    "snapshot_id": snapshot,
                    "file_sha256": batch["file_sha256"],
                    "scores": manifest,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            material = replace(
                material,
                witnesses=tuple(
                    replace(w, source_sha256=sha256(raw).hexdigest()) for w in witnesses
                ),
            )
            return material, raw

    def acquire(self, actor, batch_id, snapshot, cancelled=None) -> DatabaseSource:
        material, manifest = self.capture(actor, batch_id, snapshot)
        key = (actor.teacher_id, batch_id, snapshot)
        with self._lock:
            if key in self._consumed:
                raise APIError(
                    409, "AI_CAMPAIGN_ALREADY_USED", "该测评已发起过调查，请读取已有结果"
                )
            self._consumed.add(key)  # Failure also consumes; never refund disclosure history.
        return DatabaseSource(
            "authorized-db-" + batch_id,
            DatabaseEnvelope(
                material,
                manifest,
                "authorized_database",
                self,
                actor,
                batch_id,
                snapshot,
                cancelled,
            ),
            sha256(manifest).hexdigest(),
        )

    def replay_witnesses(self, source, references):
        # Fresh whole DB snapshot equality binds ALL requested references atomically.
        # No kernel/replay check is cached; recheck current access after resolving the batch.
        source.validate()
        index = {w.reference_id: w for w in source.envelope.material.witnesses}
        result = []
        for reference in references:
            witness = index.get(reference)
            if witness is None or witness.source_sha256 != source.fingerprint:
                raise APIError(503, "DATA_INTEGRITY_ERROR", "调查依据不可核验")
            result.append(witness)
        source.validate()
        return tuple(result)


class ProductionDatabaseProvider(DatabaseProvider):
    """Real-batch provider; only sources named by an operator RealDataAdmission pass.

    The admission is loaded outside the repository and is separate from the
    per-task LiveAdmission. It never converts a browser flag into permission.
    """

    def __init__(self, service, admission: RealDataAdmission):
        if type(admission) is not RealDataAdmission:
            raise ValueError("real_data_admission_required")
        super().__init__(service, (), allow_unregistered=True)
        self.admission = admission

    def require_registered_source(self, file_sha256: str) -> None:
        self.admission.require(file_sha256)
