"""Regenerate static synthetic inputs. This file intentionally contains no oracle data."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

LOOP_INDEX = """values = [int(x) for x in input().split()]
total = values[0]
for i in range(1, len(values)):
    total += values[i]
print(total)
"""
NO_LOOP = """values = [int(x) for x in input().split()]
print(sum(values))
"""
BOUNDARY = """values = [int(x) for x in input().split()]
total = 0
for i in range(len(values) + 1):
    total += values[i]
print(total)
"""
INPUT_PARSE = """text = input()
left, right = text.split()
print(int(left) + int(right))
"""


def test(point: str, status: str) -> dict:
    return {"point": point, "status": status}


def row(
    case_id: str,
    problem: str,
    number: int,
    verdict: str,
    code: str | None,
    tests: list[dict],
    *,
    language: str | None = "python",
    code_state: str = "present",
    code_truncated: bool = False,
    witness_fault: str | None = None,
) -> dict:
    participant = f"anon-{number:02d}"
    submission_id = f"{case_id}-{problem}-{number:02d}"
    result = {
        "submission_id": submission_id,
        "participant": participant,
        "problem": problem,
        "language": language,
        "code": code,
        "code_state": code_state,
        "code_truncated": code_truncated,
        "verdict": verdict,
        "tests": tests,
        "source_id": f"synthetic-{case_id}-{number:02d}",
        "location": f"synthetic/{case_id}/{problem}/{number:02d}.html",
    }
    if witness_fault is not None:
        result["witness_fault"] = witness_fault
    return result


def write_case(
    case_id: str,
    distribution: str,
    problems: list[str],
    participants: int,
    submissions: list[dict],
) -> None:
    payload = {
        "schema_version": 1,
        "case_id": case_id,
        "distribution": distribution,
        "batch_id": f"ds06a-{case_id}",
        "participants": [f"anon-{index:02d}" for index in range(1, participants + 1)],
        "problems": problems,
        "submissions": submissions,
    }
    path = ROOT / "cases" / f"{case_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def standard_rows(case_id: str, problem: str, point: str, failure: str) -> list[dict]:
    return [
        row(
            case_id,
            problem,
            index,
            failure if index <= 5 else "accepted",
            LOOP_INDEX if index <= 5 else NO_LOOP,
            [test(point, failure if index <= 5 else "accepted")],
        )
        for index in range(1, 11)
    ]


def main() -> None:
    case = "same_verdict_different_features"
    write_case(case, "balanced_10x1_50pct_failure", ["ordinary-sum"], 10, standard_rows(case, "ordinary-sum", "ordinary", "wrong_answer"))

    case = "same_structure_different_points"
    rows = []
    for index in range(1, 11):
        failed_point = "empty-input" if index <= 5 else "last-element"
        rows.append(
            row(
                case,
                "indexed-sum",
                index,
                "wrong_answer",
                LOOP_INDEX,
                [
                    test("empty-input", "wrong_answer" if failed_point == "empty-input" else "accepted"),
                    test("last-element", "wrong_answer" if failed_point == "last-element" else "accepted"),
                ],
            )
        )
    write_case(case, "two_failure_clusters_10x1", ["indexed-sum"], 10, rows)

    case = "manual_boundary_index_input"
    rows = [
        row(case, "two-column-sum", index, "wrong_answer", BOUNDARY, [test("last-element", "wrong_answer")])
        for index in range(1, 6)
    ] + [
        row(case, "two-column-sum", index, "runtime_error", INPUT_PARSE, [test("single-token", "runtime_error")])
        for index in range(6, 11)
    ]
    write_case(case, "two_manual_defect_types_10x1", ["two-column-sum"], 10, rows)

    case = "association_not_causation"
    write_case(case, "confounded_10x1_50pct_failure", ["long-sequence"], 10, standard_rows(case, "long-sequence", "large-case", "wrong_answer"))

    case = "incomplete_code"
    rows = [
        row(case, "boundary-sum", 1, "wrong_answer", None, [test("boundary", "wrong_answer")], code_state="missing"),
        row(case, "boundary-sum", 2, "wrong_answer", LOOP_INDEX, [test("boundary", "wrong_answer")], code_truncated=True),
        row(case, "boundary-sum", 3, "wrong_answer", LOOP_INDEX, [test("boundary", "wrong_answer")], language=None),
        row(case, "boundary-sum", 4, "wrong_answer", "for i in range(:\n", [test("boundary", "wrong_answer")]),
        row(case, "boundary-sum", 5, "wrong_answer", LOOP_INDEX, [test("boundary", "wrong_answer")]),
    ] + [
        row(case, "boundary-sum", index, "accepted", NO_LOOP, [test("boundary", "accepted")])
        for index in range(6, 11)
    ]
    write_case(case, "mixed_incomplete_evidence_10x1", ["boundary-sum"], 10, rows)

    case = "insufficient_test_evidence"
    rows = [
        row(case, "compile-check", index, "compile_error", None, [], code_state="missing")
        for index in range(1, 6)
    ] + [
        row(case, "compile-check", index, "accepted", NO_LOOP, [])
        for index in range(6, 11)
    ]
    write_case(case, "no_test_points_10x1", ["compile-check"], 10, rows)

    case = "small_sample_privacy"
    rows = [
        row(case, "tiny-sum", index, "wrong_answer" if index <= 2 else "accepted", LOOP_INDEX if index <= 2 else NO_LOOP, [test("tiny-case", "wrong_answer" if index <= 2 else "accepted")])
        for index in range(1, 4)
    ]
    write_case(case, "privacy_limited_3x1", ["tiny-sum"], 3, rows)

    case = "varied_distribution"
    rows = standard_rows(case, "alpha", "tail", "wrong_answer")
    for index in range(1, 16):
        verdict = "timeout" if index <= 5 else "accepted" if index <= 10 else "not_attempted"
        tests = [] if index > 10 else [test("large", verdict)]
        rows.append(
            row(
                case,
                "beta",
                index,
                verdict,
                LOOP_INDEX if index <= 10 else None,
                tests,
                code_state="present" if index <= 10 else "unanswered",
            )
        )
    write_case(case, "two_problems_10_and_15_rows", ["alpha", "beta"], 15, rows)

    case = "duplicate_submission"
    first = row(case, "duplicate", 1, "wrong_answer", LOOP_INDEX, [test("one", "wrong_answer")])
    second = dict(first)
    write_case(case, "structural_rejection", ["duplicate"], 1, [first, second])

    case = "missing_witness"
    rows = standard_rows(case, "missing-source", "one", "wrong_answer")
    rows[0]["witness_fault"] = "missing_verdict"
    write_case(case, "source_missing_rejection", ["missing-source"], 10, rows)

    case = "witness_mismatch"
    rows = standard_rows(case, "source-check", "one", "wrong_answer")
    rows[0]["witness_fault"] = "verdict"
    write_case(case, "source_integrity_rejection", ["source-check"], 10, rows)


if __name__ == "__main__":
    main()
