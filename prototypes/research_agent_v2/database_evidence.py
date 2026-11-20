"""Read and replay B-03B answer-sheet evidence for one offline programming task."""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from hashlib import sha256
from io import BytesIO
from typing import cast, get_args
from zipfile import BadZipFile, ZipFile

from app.ai.edu.discovery.models import AgentError
from app.analysis.code.contract import SourceRef, SubmissionRecord
from app.analysis.code.engine import analyze
from app.analysis.code.features import FeatureId
from app.analysis.code.interop import (
    b01_code_state,
    b01_language,
    b01_test_point_result,
    b01_verdict,
    c01_feature_associations,
    c01_feature_code,
    c01_status,
    c01_test_point_status,
)
from app.core.errors import APIError
from app.edu.assessment_models import AssessmentOverview, AssetInfo, Observation
from app.edu.assessment_models import SubmissionEvidence as StoredSubmission
from app.edu.assessments import AssessmentReader, AssetInput, _source_snapshot_id
from app.edu.auth import Principal, require_scope, require_teacher
from app.importing.edu.answersheet import (
    CODE_PRESENT,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNKNOWN,
    AnswerSheetEvidence,
    ObservationRecord,
    classify_status,
    merge_answersheets,
    parse_answersheet,
)
from app.importing.edu.answersheet import SubmissionRecord as ParsedSubmission
from app.importing.edu.detect import ZIP_HTML
from app.importing.edu.types import ImportFileError
from app.storage.database import utc_now

from prototypes.adaptive_assessment.data import Witness

from .programming_evidence import (
    EvidenceScope,
    EvidenceSnapshot,
    FeatureAssociation,
    FeatureCode,
    FindingStatus,
    SubmissionEvidence,
    TestEvidence,
    TestStatus,
    Verdict,
)

_FAILURES = frozenset(get_args(FindingStatus))
_DUPLICATE_BLOCK_CODES = {"DUPLICATE_BLOCK_IGNORED", "DUPLICATE_BLOCK_CONFLICT"}


def _json_hash(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _read_error(error: APIError) -> AgentError:
    if error.status_code in (401, 403, 404):
        return AgentError("access_denied")
    if error.code == "ORIGINAL_UNAVAILABLE":
        return AgentError("programming_original_unavailable")
    if error.code == "SOURCE_CHANGED":
        return AgentError("source_validation_failed")
    return AgentError("programming_evidence_read_failed")


@dataclass(frozen=True, repr=False)
class AssessmentEvidenceProvider:
    """Server-owned binding. Each capture rechecks permission and original bytes."""

    reader: AssessmentReader
    principal: Principal
    scope: EvidenceScope

    def _overview(self, scope: EvidenceScope) -> AssessmentOverview:
        if scope != self.scope or scope.actor != self.principal.teacher_id or not scope.task:
            raise AgentError("access_denied")
        with self.reader.database.transaction() as connection:
            require_teacher(connection, self.principal, utc_now())
            assessment = self.reader._owned(connection, self.principal, scope.batch)
            require_scope(connection, self.principal, assessment["data_scope_id"])
        overview = self.reader.overview(self.principal, scope.batch)
        manifest = [
            AssetInput(a.entry, a.kind, a.sha256, a.byte_size, a.source_index)
            for a in overview.assets
        ]
        if (
            not overview.assets
            or overview.source_snapshot_id != scope.snapshot
            or _source_snapshot_id(scope.batch, manifest) != scope.snapshot
        ):
            raise AgentError("source_validation_failed")
        if any(a.storage_state != "stored" for a in overview.assets):
            raise AgentError("programming_original_unavailable")
        if overview.counts_provisional:
            raise AgentError("programming_assessment_needs_review")
        return overview

    def require_details(self, scope: EvidenceScope) -> None:
        # B-03B permits individual evidence only to the authenticated owner.
        try:
            self._overview(scope)
        except APIError as error:
            raise _read_error(error) from None

    def capture(self, scope: EvidenceScope) -> EvidenceSnapshot:
        try:
            overview = self._overview(scope)
            snapshot = self._capture(scope, overview)
            if self._overview(scope) != overview:
                raise AgentError("source_validation_failed")
            return snapshot
        except APIError as error:
            raise _read_error(error) from None
        except (ImportFileError, BadZipFile, KeyError, OSError):
            raise AgentError("source_validation_failed") from None

    def _capture(self, scope: EvidenceScope, overview: AssessmentOverview) -> EvidenceSnapshot:
        documents, assets_by_person, entry_hashes = self._read_originals(scope, overview.assets)
        raw = documents[0] if len(documents) == 1 else merge_answersheets(documents)
        raw_people = {row.participant_key: row for row in raw.students}
        raw_submissions = {(row.participant_key, row.problem_label): row for row in raw.submissions}
        raw_points = defaultdict(list)
        for point in raw.observations:
            raw_points[point.participant_key, point.problem_label].append(point)

        people = self.reader.participants(self.principal, scope.batch).items
        problems = tuple(
            row.problem_label
            for row in self.reader.problem_patterns(self.principal, scope.batch).items
        )
        if (
            len(people) != overview.student_count
            or len({person.student_pk for person in people}) != len(people)
            or sorted(person.external_key for person in people) != sorted(raw_people)
            or len(problems) != overview.problem_count
            or problems != tuple(problem.label for problem in raw.problems)
        ):
            raise AgentError("source_validation_failed")

        witnesses: dict[str, Witness] = {}
        rows, records = [], defaultdict(list)
        observation_count = 0
        for person in people:
            if person.display_name != raw_people[person.external_key].display_name:
                raise AgentError("source_validation_failed")
            asset = assets_by_person[person.external_key]
            detail = self.reader.student(
                self.principal,
                scope.batch,
                person.student_pk,
                asset_sha256=asset.sha256,
                source_snapshot_id=scope.snapshot,
            )
            if (detail.student_pk, detail.external_key, detail.display_name) != (
                person.student_pk,
                person.external_key,
                person.display_name,
            ):
                raise AgentError("source_validation_failed")
            for stored in detail.submissions:
                key = (person.external_key, stored.problem_label)
                original = raw_submissions.pop(key, None)
                if original is None:
                    raise AgentError("source_validation_failed")
                points = raw_points.pop(key, [])
                _verify_submission(stored, original, asset, points)
                address = _EvidenceAddress(
                    scope, asset, original, entry_hashes[asset.asset_id, original.source_entry]
                )
                row, record = address.project(person.student_pk, stored, witnesses)
                rows.append(row)
                records[row.problem].append(record)
                observation_count += len(row.tests)
        if (
            raw_submissions
            or raw_points
            or len(rows) != overview.submission_count
            or observation_count != overview.observation_count
        ):
            raise AgentError("source_validation_failed")
        features = _project_features(scope.batch, rows, records, witnesses)
        return EvidenceSnapshot(
            scope.snapshot,
            problems,
            tuple(person.student_pk for person in people),
            tuple(rows),
            tuple(witnesses.values()),
            tuple(features),
        )

    def _read_originals(
        self, scope: EvidenceScope, assets: list[AssetInfo]
    ) -> tuple[list[AnswerSheetEvidence], dict[str, AssetInfo], dict[tuple[str, str], str]]:
        documents, assets_by_person, entry_hashes = [], {}, {}
        for asset in assets:
            original = self.reader.original(
                self.principal, scope.batch, asset.asset_id, expected_sha256=asset.sha256
            )
            if (
                original.source_snapshot_id != scope.snapshot
                or original.byte_size != asset.byte_size
                or original.kind != ZIP_HTML
            ):
                raise AgentError("source_validation_failed")
            document = parse_answersheet(original.payload)
            if any(
                diagnostic.code in _DUPLICATE_BLOCK_CODES for diagnostic in document.diagnostics
            ):
                raise AgentError("programming_duplicate_submission")
            for person in document.students:
                if person.participant_key in assets_by_person:
                    raise AgentError("programming_duplicate_submission")
                assets_by_person[person.participant_key] = asset
            with ZipFile(BytesIO(original.payload)) as archive:
                names = Counter(archive.namelist())
                for entry in {submission.source_entry for submission in document.submissions}:
                    if names[entry] != 1:
                        raise AgentError("programming_ambiguous_source")
                    entry_hashes[asset.asset_id, entry] = sha256(archive.read(entry)).hexdigest()
            documents.append(document)
        return documents, assets_by_person, entry_hashes


def _verify_submission(
    stored: StoredSubmission,
    original: ParsedSubmission,
    asset: AssetInfo,
    points: list[ObservationRecord],
) -> None:
    address = stored.source
    if address is None or address.asset_id is None:
        raise AgentError("programming_ambiguous_source")
    if (
        (address.asset_id, address.asset_sha256, address.entry, address.source_index)
        != (asset.asset_id, asset.sha256, original.source_entry, original.source_index)
        or (stored.code, stored.code_state, stored.code_truncated, stored.language)
        != (original.code or "", original.code_state, original.code_truncated, original.language)
        or (stored.verdict, stored.verdict_class, stored.score, stored.score_total)
        != (
            original.verdict,
            classify_status(original.verdict),
            original.score,
            original.score_total,
        )
        or (stored.compiler_output, stored.compiler_output_truncated)
        != (original.compiler_output, original.compiler_output_truncated)
        or len(stored.observations) != len(points)
    ):
        raise AgentError("source_validation_failed")
    for observed, replayed in zip(
        stored.observations, sorted(points, key=lambda point: point.test_index), strict=True
    ):
        if _stored_point(observed) != _original_point(replayed):
            raise AgentError("source_validation_failed")


def _stored_point(row: Observation) -> tuple:
    return (
        row.source_row,
        row.test_index,
        row.status,
        row.status_known,
        row.status_class,
        row.score,
        row.duration_text,
        row.duration_us,
        row.memory_text,
        row.memory_kb,
    )


def _original_point(row: ObservationRecord) -> tuple:
    return (
        row.source_row,
        row.test_index,
        row.status,
        row.status_known,
        classify_status(row.status, known=row.status_known),
        row.score,
        row.duration_text,
        None if row.duration_ms is None else round(row.duration_ms * 1000),
        row.memory_text,
        row.memory_kb,
    )


@dataclass(frozen=True, repr=False)
class _EvidenceAddress:
    scope: EvidenceScope
    asset: AssetInfo
    original: ParsedSubmission
    entry_hash: str

    def project(
        self, person: str, stored: StoredSubmission, witnesses: dict[str, Witness]
    ) -> tuple[SubmissionEvidence, SubmissionRecord]:
        problem = stored.problem_label
        locator = f"{self.original.source_entry}#block/{self.original.source_index}"

        def add(kind: str, suffix: str, value: str | None, content: str) -> str:
            position = f"{locator}/{suffix}"
            ref = _json_hash([self.scope.snapshot, self.asset.asset_id, position, kind])
            proof = Witness(
                ref,
                self.asset.asset_id,
                self.asset.sha256,
                position,
                self.entry_hash,
                problem,
                person,
                kind,
                value,
                sha256(content.encode()).hexdigest(),
            )
            if witnesses.setdefault(ref, proof) != proof:
                raise AgentError("conflicting_witness_source")
            return ref

        verdict = b01_verdict(stored.verdict if stored.verdict_class != STATUS_UNKNOWN else None)
        status = cast(Verdict, c01_status(verdict))
        verdict_ref = add("verdict", "verdict", status, stored.verdict or "")
        code_ref = None
        if stored.code_state == CODE_PRESENT and not stored.code_truncated and stored.code:
            code_ref = add("code", "code", stored.code, stored.code)
        tests, points = [], []
        for row in stored.observations:
            point = b01_test_point_result(
                str(row.test_index),
                row.status
                if row.status_known and row.status_class in (STATUS_PASS, STATUS_FAIL)
                else None,
            )
            point_status = cast(TestStatus, c01_test_point_status(point))
            ref = add(
                "test",
                f"result/{row.source_row}",
                json.dumps([point.testpoint_id, point_status]),
                json.dumps(_stored_point(row), ensure_ascii=False, separators=(",", ":")),
            )
            tests.append(TestEvidence(point.testpoint_id, point_status, ref))
            points.append(point)

        compiler = "C++" if stored.language == "CLANGXX" else stored.language
        submission_id = _json_hash([self.asset.asset_id, locator])
        return (
            SubmissionEvidence(person, problem, status, verdict_ref, code_ref, tuple(tests)),
            SubmissionRecord(
                self.scope.batch,
                problem,
                submission_id,
                person,
                b01_language(compiler),
                stored.code,
                verdict,
                tuple(points),
                SourceRef(self.asset.asset_id, submission_id),
                b01_code_state(stored.code_state),
                stored.code_truncated,
            ),
        )


def _project_features(
    batch: str,
    rows: list[SubmissionEvidence],
    records: dict[str, list[SubmissionRecord]],
    witnesses: dict[str, Witness],
) -> list[FeatureAssociation]:
    associations = []
    rows_by_problem = defaultdict(dict)
    for row in rows:
        rows_by_problem[row.problem][row.participant] = row
    feature_ids = {c01_feature_code(feature): feature for feature in FeatureId}
    for problem, submissions in records.items():
        analysis = analyze(batch, problem, submissions)
        by_person = {item.reference.student_id: item for item in analysis.submissions}
        problem_rows = rows_by_problem[problem]
        directions = {
            (test.point, test.status)
            for row in problem_rows.values()
            for test in row.tests
            if test.status in _FAILURES
        }
        fallbacks = {
            row.verdict
            for row in problem_rows.values()
            if not row.tests and row.verdict in _FAILURES | {"compile_error"}
        }
        no_points = replace(
            analysis,
            submissions=tuple(item for item in analysis.submissions if not item.test_points),
        )
        for point, status in sorted(directions) + [(None, status) for status in sorted(fallbacks)]:
            view = no_points if point is None else analysis
            for feature in c01_feature_associations(view, point=point, status=status):
                if not feature.checked:
                    continue
                present = []
                for person, location in feature.present:
                    item = by_person[person]
                    code_ref = problem_rows[person].code_ref
                    if code_ref is None or location != item.reference.source.location:
                        raise AgentError("unverified_feature_source")
                    code = witnesses[code_ref]
                    measurement = item.features.get(feature_ids[feature.code])
                    ref = _json_hash([code_ref, "feature", feature.code])
                    proof = replace(
                        code,
                        reference_id=ref,
                        locator=f"{code.locator}/feature/{feature.code}",
                        kind="feature",
                        value=feature.code,
                        content_sha256=_json_hash(
                            [
                                code.content_sha256,
                                feature.code,
                                measurement.value,
                                measurement.confidence.value,
                            ]
                        ),
                    )
                    witnesses[ref] = proof
                    present.append((person, ref))
                associations.append(
                    FeatureAssociation(
                        problem,
                        point,
                        status,
                        cast(FeatureCode, feature.code),
                        feature.checked,
                        tuple(present),
                    )
                )
    return associations
