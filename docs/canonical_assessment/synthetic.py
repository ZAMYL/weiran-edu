"""Reproducible synthetic scenarios. No student files, database or model clients."""

import json
from dataclasses import replace
from typing import Literal

from .model import (
    PROFILE,
    AssessmentItem,
    Batch,
    Dataset,
    Evidence,
    Observation,
    Participant,
    Rubric,
    Scale,
)
from .readiness import assess


def fixture(
    item_count: int = 5,
    participant_count: int = 3,
    *,
    kind: Literal["competency", "question", "task"] = "competency",
    comments: bool = False,
    responses: bool = False,
    criteria: bool = False,
    recommended: bool = True,
) -> Dataset:
    items, observations, evidence, rubrics = [], [], [], []
    participants = tuple(
        Participant(f"synthetic-p-{p}", "synthetic-scope") for p in range(participant_count)
    )
    for index in range(item_count):
        item_id = f"synthetic-item-{index}"
        code = list(PROFILE)[index] if recommended and kind == "competency" and index < 5 else None
        scale = Scale(f"synthetic-scale-{index}", "1", "5", "0.01", "level")
        if kind == "question":
            scale = Scale(f"synthetic-scale-{index}", "0", "100", "0.25", "points")
        rubric_id = f"synthetic-rubric-{index}" if criteria else None
        items.append(
            AssessmentItem(
                item_id,
                PROFILE[code] if code else f"自定义项目 {index + 1}",
                kind,
                scale,
                rubric_id,
                code,
            )
        )
        if criteria:
            criteria_id = f"synthetic-criteria-{index}"
            evidence.append(
                Evidence(
                    criteria_id,
                    "synthetic-rubric-source",
                    "criteria",
                    item_id,
                    None,
                    ("teacher_analysis",),
                    "user_confirmed",
                )
            )
            rubrics.append(Rubric(rubric_id, "synthetic.v1", (item_id,), criteria_id))
        for p, participant in enumerate(participants):
            observation_id = f"synthetic-observation-{p}-{index}"
            refs = []
            for evidence_kind, included in (
                ("score", True),
                ("comment", comments),
                ("response", responses),
            ):
                if included:
                    evidence_id = f"{observation_id}-{evidence_kind}"
                    refs.append(evidence_id)
                    evidence.append(
                        Evidence(
                            evidence_id,
                            "synthetic-score-source",
                            evidence_kind,
                            item_id,
                            participant.participant_id,
                            ("teacher_analysis",),
                            "user_confirmed",
                        )
                    )
            observations.append(
                Observation(
                    observation_id,
                    participant.participant_id,
                    item_id,
                    ("2.75", "3.00", "4.25")[p % 3],
                    tuple(refs),
                )
            )
    return Dataset(
        "synthetic-dataset",
        "synthetic.v1",
        Batch(
            "synthetic-batch", "synthetic.v1", ("synthetic-score-source", "synthetic-rubric-source")
        ),
        participants,
        tuple(items),
        tuple(observations),
        tuple(evidence),
        tuple(rubrics),
    )


def scenarios() -> dict[str, Dataset]:
    complete = fixture(comments=True, responses=True, criteria=True)
    missing = replace(complete.observations[0], raw_decimal=None)
    return {
        "A_three_items_no_comments": fixture(3),
        "B_five_items_review_resources": complete,
        "C_eight_custom_items": fixture(8, recommended=False),
        "D_thirteen_questions": fixture(13, kind="question"),
        "E_scores_without_responses": fixture(comments=True, criteria=True),
        "F_defined_five_missing_score": replace(
            complete, observations=(missing, *complete.observations[1:])
        ),
        "G_no_criteria": fixture(comments=True, responses=True),
        "H_history_without_comparability": replace(
            complete, history_batch_ids=("synthetic-earlier-batch",)
        ),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            {name: assess(dataset).to_dict() for name, dataset in scenarios().items()},
            ensure_ascii=False,
            indent=2,
        )
    )
