"""Offline acceptance bridge for B-01 ZIP, DS-03 analysis and C-01 evidence."""

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from io import BytesIO
from typing import cast, get_args
from zipfile import ZipFile

from prototypes.adaptive_assessment.data import Witness
from prototypes.research_agent_v2.programming_evidence import (
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

from app.ai.edu.discovery.models import AgentError
from app.analysis.code.contract import SourceRef, SubmissionRecord, TestPointResult
from app.analysis.code.engine import analyze
from app.analysis.code.features import FeatureId
from app.analysis.code.interop import (
    b01_code_state,
    b01_language,
    b01_test_point_outcome,
    b01_verdict,
    c01_feature_associations,
    c01_feature_code,
    c01_status,
)
from app.analysis.code.results import Analysis
from app.importing.edu.answersheet import (
    CODE_PRESENT,
    AnswerSheetEvidence,
    ObservationRecord,
    parse_answersheet,
)

_FAILURES = frozenset(get_args(FindingStatus))
_RUNTIME_LABELS = {"运行时错误", "段错误", "非零返回"}


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def normalize_status(raw: str | None) -> str | None:
    return "运行错误" if raw in _RUNTIME_LABELS else raw


def test_status(row: ObservationRecord) -> TestStatus:
    status = (
        c01_status(b01_verdict(normalize_status(row.status))) if row.status_known else "unknown"
    )
    return cast(TestStatus, status) if status in _FAILURES | {"accepted"} else "unknown"


@dataclass(frozen=True, repr=False)
class AcceptedMaterial:
    parsed: AnswerSheetEvidence
    analyses: tuple[Analysis, ...]
    snapshot: EvidenceSnapshot


def materialize(blob: bytes, batch: str) -> AcceptedMaterial:
    parsed = parse_answersheet(blob)
    archive_hash = digest(blob)
    # Each observation is owned by the parsed submission carrying its file/block address.
    observations = defaultdict(list)
    for row in parsed.observations:
        observations[row.participant_key, row.problem_label].append(row)
    with ZipFile(BytesIO(blob)) as archive:
        entry_hashes = {
            entry: digest(archive.read(entry))
            for entry in {s.source_entry for s in parsed.submissions}
        }
    witnesses: dict[str, Witness] = {}
    rows, associations = [], []
    records = defaultdict(list)
    by_subject = {}
    entry_by_block = {}

    def witness(
        subject, entry: str, anchor: str, kind: str, value: str | None, original: str
    ) -> str:
        participant, problem = subject
        locator = f"{entry}#{anchor}"
        ref = digest(json.dumps([archive_hash, locator, kind]).encode())
        proof = Witness(
            ref,
            archive_hash,
            archive_hash,
            locator,
            entry_hashes[entry],
            problem,
            participant,
            kind,
            value,
            digest(original.encode()),
        )
        if witnesses.setdefault(ref, proof) != proof:
            raise AgentError("conflicting_witness_source")
        return ref

    for submission in parsed.submissions:
        subject = submission.participant_key, submission.problem_label
        person, problem = subject
        entry = submission.source_entry
        anchor = f"block/{submission.source_index}"
        location = f"{entry}#{anchor}"
        entry_by_block[subject] = entry, anchor
        verdict = b01_verdict(normalize_status(submission.verdict))
        verdict_ref = witness(
            subject,
            entry,
            anchor + "/verdict",
            "verdict",
            c01_status(verdict),
            submission.verdict or "",
        )
        code = submission.code
        code_ref = None
        if (
            submission.code_state == CODE_PRESENT
            and not submission.code_truncated
            and code is not None
        ):
            code_ref = witness(subject, entry, anchor + "/code", "code", None, code)
        point_evidence, points = [], []
        for row in observations.pop(subject, ()):
            point = str(row.test_index)
            status = test_status(row)
            content = json.dumps(
                [point, row.status, row.score, row.duration_text, row.memory_text],
                ensure_ascii=False,
            )
            ref = witness(
                subject,
                entry,
                f"{anchor}/result/{row.source_row}",
                "test",
                json.dumps([point, status]),
                content,
            )
            point_evidence.append(TestEvidence(point, status, ref))
            points.append(
                TestPointResult(
                    point,
                    b01_test_point_outcome(normalize_status(row.status))
                    if status != "unknown"
                    else b01_test_point_outcome(None),
                )
            )
        row = SubmissionEvidence(
            person,
            problem,
            cast(Verdict, c01_status(verdict)),
            verdict_ref,
            code_ref,
            tuple(point_evidence),
        )
        rows.append(row)
        by_subject[subject] = row
        compiler = "C++" if submission.language == "CLANGXX" else submission.language
        records[problem].append(
            SubmissionRecord(
                batch,
                problem,
                location,
                person,
                b01_language(compiler),
                code or "",
                verdict,
                tuple(points),
                SourceRef(archive_hash, location),
                b01_code_state(submission.code_state),
                submission.code_truncated,
            )
        )
    if observations:
        raise AgentError("observation_without_submission")

    analyses = tuple(
        analyze(batch, problem.label, records[problem.label]) for problem in parsed.problems
    )
    for analysis in analyses:
        cohorts = defaultdict(list)
        problem = analysis.problem_id
        for item in analysis.submissions:
            row = by_subject[item.reference.student_id, problem]
            for point in row.tests:
                if point.status in _FAILURES:
                    # This local projection describes the test verdict. The original
                    # DS-03 whole-submission association and its inputs stay intact.
                    point_status = next(
                        v for v in type(item.verdict) if c01_status(v) == point.status
                    )
                    cohorts[point.point, point.status].append(replace(item, verdict=point_status))
            if not row.tests and row.verdict in _FAILURES | {"compile_error"}:
                cohorts[None, row.verdict].append(item)
        for (point, status), cohort in cohorts.items():
            projected = c01_feature_associations(
                replace(analysis, submissions=tuple(cohort)),
                status=status,
                point=point,
            )
            features = {item.reference.student_id: item.features for item in cohort}
            for feature in projected:
                feature_id = next(f for f in FeatureId if c01_feature_code(f) == feature.code)
                present = []
                for person, location in feature.present:
                    subject = person, problem
                    entry, anchor = entry_by_block[subject]
                    if location != f"{entry}#{anchor}" or by_subject[subject].code_ref is None:
                        raise AgentError("unverified_feature_source")
                    code_source = witnesses[by_subject[subject].code_ref]
                    measurement = features[person].get(feature_id)
                    proof = json.dumps(
                        [
                            code_source.content_sha256,
                            feature.code,
                            measurement.value,
                            measurement.confidence.value,
                        ]
                    )
                    ref = witness(
                        subject,
                        entry,
                        f"{anchor}/code/feature/{feature.code}",
                        "feature",
                        feature.code,
                        proof,
                    )
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
    return AcceptedMaterial(
        parsed,
        analyses,
        EvidenceSnapshot(
            archive_hash,
            tuple(p.label for p in parsed.problems),
            tuple(s.participant_key for s in parsed.students),
            tuple(rows),
            tuple(witnesses.values()),
            tuple(associations),
        ),
    )


@dataclass(repr=False)
class LocalAcceptanceProvider:
    scope: EvidenceScope
    snapshot: EvidenceSnapshot
    read_bytes: Callable[[], bytes]
    allowed: bool = True
    details_allowed: bool = True

    def capture(self, scope: EvidenceScope) -> EvidenceSnapshot:
        if not self.allowed or scope != self.scope:
            raise AgentError("access_denied")
        if digest(self.read_bytes()) != scope.snapshot or self.snapshot.snapshot != scope.snapshot:
            raise AgentError("source_validation_failed")
        return self.snapshot

    def require_details(self, scope: EvidenceScope) -> None:
        self.capture(scope)
        if not self.details_allowed:
            raise AgentError("access_denied")
