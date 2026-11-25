"""Load synthetic inputs separately from independently authored human oracles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True)
class Case:
    raw: dict

    @property
    def case_id(self) -> str:
        return self.raw["case_id"]

    @property
    def distribution(self) -> str:
        return self.raw["distribution"]

    @property
    def batch_id(self) -> str:
        return self.raw["batch_id"]

    @property
    def problems(self) -> tuple[str, ...]:
        return tuple(self.raw["problems"])

    @property
    def submissions(self) -> tuple[dict, ...]:
        return tuple(self.raw["submissions"])

    def for_problem(self, problem: str) -> tuple[dict, ...]:
        return tuple(row for row in self.submissions if row["problem"] == problem)

    def submission(self, submission_id: str) -> dict:
        return next(row for row in self.submissions if row["submission_id"] == submission_id)


def load_cases(data_dir: Path, selected: str | None = None) -> tuple[Case, ...]:
    cases = []
    for path in sorted((data_dir / "cases").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if selected is not None and raw["case_id"] != selected:
            continue
        _validate_case(raw, path)
        cases.append(Case(raw))
    if selected is not None and not cases:
        raise ValueError(f"unknown_case:{selected}")
    return tuple(cases)


def load_oracle(data_dir: Path, case: Case) -> dict:
    path = data_dir / "oracles" / f"{case.case_id}.json"
    oracle = json.loads(path.read_text(encoding="utf-8"))
    if oracle.get("case_id") != case.case_id:
        raise ValueError(f"oracle_case_mismatch:{path.name}")
    if not oracle.get("human_review", {}).get("rationale"):
        raise ValueError(f"independent_human_rationale_required:{path.name}")
    return oracle


def snapshot_id(case: Case) -> str:
    payload = json.dumps(case.raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def _validate_case(raw: dict, path: Path) -> None:
    required = {
        "schema_version",
        "case_id",
        "distribution",
        "batch_id",
        "participants",
        "problems",
        "submissions",
    }
    if raw.get("schema_version") != 1 or not required <= raw.keys():
        raise ValueError(f"invalid_case_schema:{path.name}")
    _reject_oracle_keys(raw, path)
    participants, problems = raw["participants"], raw["problems"]
    if (
        not participants
        or not problems
        or len(participants) != len(set(participants))
        or len(problems) != len(set(problems))
    ):
        raise ValueError(f"invalid_case_scope:{path.name}")
    for row in raw["submissions"]:
        fields = {
            "submission_id",
            "participant",
            "problem",
            "language",
            "code",
            "code_state",
            "code_truncated",
            "verdict",
            "tests",
            "source_id",
            "location",
        }
        if not fields <= row.keys():
            raise ValueError(f"invalid_submission_schema:{path.name}")
        if row["participant"] not in participants or row["problem"] not in problems:
            raise ValueError(f"submission_outside_case_scope:{path.name}")
        for test in row["tests"]:
            if not {"point", "status"} <= test.keys() or not test["point"]:
                raise ValueError(f"invalid_test_schema:{path.name}")


def _reject_oracle_keys(value, path: Path) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower()
            if normalized.startswith(("expected", "oracle", "human_", "root_cause")):
                raise ValueError(f"answer_leakage_in_case:{path.name}:{key}")
            _reject_oracle_keys(child, path)
    elif isinstance(value, list):
        for child in value:
            _reject_oracle_keys(child, path)
