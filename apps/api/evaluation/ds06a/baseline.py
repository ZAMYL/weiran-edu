"""Capability A: independent deterministic judge/test-point statistics."""

from __future__ import annotations

from collections import Counter, defaultdict

from .common import (
    FAILURE_STATUSES,
    NO_TEST_FAILURES,
    evidence_ref,
    public_ref,
    rejection,
    result_shell,
)
from .model import Case


def run_baseline(case: Case) -> dict:
    result = result_shell(case.case_id, "A")
    try:
        _validate_unique(case)
    except ValueError as error:
        return rejection(case.case_id, "A", str(error))
    result["statistics"] = statistics(case)
    result["phenomena"] = phenomena(case)
    return result


def _validate_unique(case: Case) -> None:
    submission_keys, participant_keys = set(), set()
    for row in case.submissions:
        submission_key = (row["problem"], row["submission_id"])
        participant_key = (row["problem"], row["participant"])
        if submission_key in submission_keys or participant_key in participant_keys:
            raise ValueError("duplicate_submission")
        submission_keys.add(submission_key)
        participant_keys.add(participant_key)


def statistics(case: Case) -> dict:
    output = {}
    for problem in case.problems:
        rows = case.for_problem(problem)
        verdicts = Counter(row["verdict"] for row in rows)
        points = defaultdict(lambda: {"passed": 0, "failed": 0, "unknown": 0})
        for row in rows:
            for test in row["tests"]:
                if test["status"] == "accepted":
                    outcome = "passed"
                elif test["status"] in FAILURE_STATUSES:
                    outcome = "failed"
                else:
                    outcome = "unknown"
                points[test["point"]][outcome] += 1
        output[problem] = {
            "verdict_counts": dict(sorted(verdicts.items())),
            "test_points": {point: points[point] for point in sorted(points)},
        }
    return output


def phenomena(case: Case) -> list[dict]:
    output = []
    for problem in case.problems:
        rows = case.for_problem(problem)
        coverage = defaultdict(list)
        affected = defaultdict(list)
        attempted = []
        without_tests = defaultdict(list)
        for row in rows:
            if row["verdict"] not in {"not_attempted", "unknown"}:
                attempted.append(evidence_ref(row, "verdict"))
                if not row["tests"] and row["verdict"] in NO_TEST_FAILURES:
                    without_tests[row["verdict"]].append(evidence_ref(row, "verdict"))
            for test in row["tests"]:
                if test["status"] == "unknown":
                    continue
                ref = evidence_ref(row, "test", point=test["point"])
                coverage[test["point"]].append(ref)
                if test["status"] in FAILURE_STATUSES:
                    affected[test["point"], test["status"]].append(ref)
        for (point, status), refs in affected.items():
            output.append(_phenomenon(problem, point, status, coverage[point], refs))
        for status, refs in without_tests.items():
            output.append(_phenomenon(problem, None, status, attempted, refs))
    return sorted(output, key=_phenomenon_key)


def _phenomenon(problem, point, status, coverage, affected) -> dict:
    return {
        "problem": problem,
        "point": point,
        "status": status,
        "covered": [public_ref(ref) for ref in sorted(coverage, key=_ref_key)],
        "affected": [public_ref(ref) for ref in sorted(affected, key=_ref_key)],
    }


def _phenomenon_key(item: dict) -> tuple:
    return (item["problem"], item["point"] or "", item["status"])


def _ref_key(ref: dict) -> tuple:
    return (ref["participant_id"], ref["reference_id"])
