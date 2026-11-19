from collections.abc import Callable

import pytest

from app.analysis.code import (
    Language,
    SourceRef,
    SubmissionRecord,
    TestPointOutcome,
    TestPointResult,
    Verdict,
)


@pytest.fixture
def make_submission() -> Callable[..., SubmissionRecord]:
    def make(
        submission_id: str,
        code: str,
        verdict: Verdict = Verdict.ACCEPTED,
        *,
        language: Language = Language.PYTHON,
        student_id: str | None = None,
        problem_id: str = "problem-1",
        test_points: tuple[TestPointResult, ...] = (),
    ) -> SubmissionRecord:
        return SubmissionRecord(
            "batch-1",
            problem_id,
            submission_id,
            student_id or f"student-{submission_id}",
            language,
            code,
            verdict,
            test_points,
            SourceRef("synthetic-source", submission_id),
        )

    return make


def tp(testpoint_id: str, outcome: TestPointOutcome) -> TestPointResult:
    return TestPointResult(testpoint_id, outcome)
