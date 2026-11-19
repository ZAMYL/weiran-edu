"""Capability B: baseline plus the real DS-03 analysis and C-01 projection interfaces."""

from __future__ import annotations

from app.analysis.code import (
    CodeState,
    Language,
    SourceRef,
    SubmissionRecord,
    TestPointOutcome,
    TestPointResult,
    Verdict,
    c01_feature_associations,
)
from app.analysis.code import analyze as ds03_analyze

from .baseline import run_baseline
from .common import rejection, result_shell
from .model import Case

_LANGUAGES = {"python": Language.PYTHON, "cpp": Language.CPP, None: None}
_VERDICTS = {
    "accepted": Verdict.ACCEPTED,
    "wrong_answer": Verdict.WRONG_ANSWER,
    "timeout": Verdict.TIME_LIMIT_EXCEEDED,
    "memory_limit": Verdict.MEMORY_LIMIT_EXCEEDED,
    "runtime_error": Verdict.RUNTIME_ERROR,
    "format_error": Verdict.FORMAT_ERROR,
    "compile_error": Verdict.COMPILE_ERROR,
    "partial": Verdict.PARTIAL,
    "not_attempted": Verdict.NOT_ATTEMPTED,
    "unknown": Verdict.UNKNOWN,
    # DS-03 has no distinct OtherFailure enum; do not invent a more specific verdict.
    "other_failure": Verdict.UNKNOWN,
}
_CODE_STATES = {
    "present": CodeState.PRESENT,
    "unanswered": CodeState.UNANSWERED,
    "missing": CodeState.MISSING,
}


def build_ds03(case: Case) -> tuple[dict, dict]:
    baseline = run_baseline(case)
    if baseline["status"] == "rejected":
        return rejection(case.case_id, "B", baseline["error"]), {}
    result = result_shell(case.case_id, "B")
    result["statistics"] = baseline["statistics"]
    result["phenomena"] = baseline["phenomena"]
    analyses = {}
    try:
        for problem in case.problems:
            analysis = ds03_analyze(
                case.batch_id,
                problem,
                tuple(_record(case, row) for row in case.for_problem(problem)),
            )
            analyses[problem] = analysis
            _collect_analysis(result, analysis)
            for phenomenon in (
                item for item in baseline["phenomena"] if item["problem"] == problem
            ):
                for association in c01_feature_associations(
                    analysis,
                    status=phenomenon["status"],
                    point=phenomenon["point"],
                ):
                    result["feature_cohorts"].append(
                        {
                            "problem": association.problem,
                            "point": association.point,
                            "status": association.status,
                            "feature": association.code,
                            "checked": list(association.checked),
                            "present": [
                                {"participant": participant, "location": location}
                                for participant, location in association.present
                            ],
                        }
                    )
    except (ValueError, TypeError) as error:
        code = getattr(error, "code", str(error))
        return rejection(case.case_id, "B", code), {}
    result["root_assertions"] = sum(
        candidate["hypothesis_count"] for candidate in result["candidates"]
    )
    return result, analyses


def run_ds03(case: Case) -> dict:
    return build_ds03(case)[0]


def _record(case: Case, row: dict) -> SubmissionRecord:
    return SubmissionRecord(
        batch_id=case.batch_id,
        problem_id=row["problem"],
        submission_id=row["submission_id"],
        student_id=row["participant"],
        language=_LANGUAGES[row["language"]],
        source_code=row["code"] or "",
        verdict=_VERDICTS[row["verdict"]],
        test_points=tuple(
            TestPointResult(test["point"], _test_outcome(test["status"])) for test in row["tests"]
        ),
        source=SourceRef(row["source_id"], row["location"]),
        code_state=_CODE_STATES[row["code_state"]],
        code_truncated=row["code_truncated"],
    )


def _test_outcome(status: str) -> TestPointOutcome:
    if status == "accepted":
        return TestPointOutcome.PASSED
    if status in {
        "wrong_answer",
        "timeout",
        "memory_limit",
        "runtime_error",
        "format_error",
        "other_failure",
    }:
        return TestPointOutcome.FAILED
    return TestPointOutcome.UNKNOWN


def _collect_analysis(result: dict, analysis) -> None:
    for submission in analysis.submissions:
        submission_id = submission.reference.submission_id
        result["features"][submission_id] = {
            value.feature.value: value.value for value in submission.features.values
        }
        result["feature_status"][submission_id] = submission.features.status.value
    for candidate in analysis.candidates:
        evidence = []
        for source in candidate.evidence:
            submission = next(
                item for item in analysis.submissions if item.reference.source == source
            )
            evidence.append(
                {
                    "submission_id": submission.reference.submission_id,
                    "participant": submission.reference.student_id,
                    "source_id": source.source_id,
                    "location": source.location,
                }
            )
        result["candidates"].append(
            {
                "problem": analysis.problem_id,
                "feature": candidate.feature.value,
                "evidence": evidence,
                "hypothesis_count": len(candidate.hypothesis),
                "limits": list(candidate.limits),
            }
        )
