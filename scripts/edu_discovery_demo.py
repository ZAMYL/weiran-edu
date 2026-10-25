"""Offline aggregate demo, using only the existing fictional 53-person fixture."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.analysis.edu import (
    AssessmentRecord,
    Condition,
    Dimension,
    Operator,
    ScorePolicy,
    prepare_dataset,
)
from app.analysis.edu.discovery import BoundaryRequest, Policy, discover, present
from edu_demo_fixtures import make_fixture


def demo() -> dict[str, object]:
    fixture = make_fixture(53, 42)
    dataset = prepare_dataset(
        tuple(
            AssessmentRecord(
                "synthetic-53",
                f"synthetic-{i:04}",
                f"record-{i}",
                "synthetic-source",
                dict(zip(Dimension, scores, strict=True)),
            )
            for i, scores in enumerate(fixture.scores)
        ),
        policy=ScorePolicy(step=0.01),
    )
    boundary = BoundaryRequest(
        (
            Condition(Dimension.ALGORITHMS, Operator.LE, 2.75),
            Condition(Dimension.STRATEGY, Operator.GE, 4),
        ),
        Dimension.ALGORITHMS,
    )
    result = discover(
        dataset,
        batch_id="synthetic-53",
        snapshot_id="synthetic-snapshot-v1",
        policy=Policy(maximum_findings=50),
        boundaries=(boundary,),
    )
    return {
        "synthetic_only": True,
        "student_count": 53,
        "qualified_count": result.qualified_count,
        "search_attempts": len(result.search_audit),
        "findings": [present(d) for d in result.findings],
    }


def examples() -> dict[str, object]:
    cases = {
        "dimension_relation": [(1 + i % 5, 5 - i % 5, 3, 3, 3) for i in range(17)],
        "group_difference": [(1, 5, 3, 3, 3)] * 8 + [(5, 1, 3, 3, 3)] * 9,
        "rare_combination": [(3, 3, 3, 3, 3)] * 16 + [(5, 1, 3, 3, 3)],
    }
    cards = []
    for kind, rows in cases.items():
        dataset = prepare_dataset(
            tuple(
                AssessmentRecord(
                    "synthetic-17",
                    f"anonymous-{i:04}",
                    f"record-{i}",
                    "synthetic-source",
                    dict(zip(Dimension, scores, strict=True)),
                )
                for i, scores in enumerate(rows)
            ),
            policy=ScorePolicy(step=0.01),
        )
        result = discover(
            dataset,
            batch_id="synthetic-17",
            snapshot_id="synthetic-snapshot-v1",
            policy=Policy(maximum_findings=50),
        )
        cards.append(present(next(d for d in result.findings if d.type == kind)))
    known = demo()
    cards.append(
        next(d for d in known["findings"] if d["type"] == "boundary_sensitivity")
    )
    return {"synthetic_only": True, "examples": cards, "known_53": known}


if __name__ == "__main__":
    print(json.dumps(examples(), ensure_ascii=False, indent=2))
