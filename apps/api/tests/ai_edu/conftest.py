"""All inputs are synthetic; no real student text or network clients."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo

import pytest
from prototypes.adaptive_assessment.live import budget as live_budget

from app.ai.edu import (
    EvidenceDocument,
    EvidenceGrant,
    RubricDocument,
    RubricGrant,
    SyntheticAccess,
    SyntheticInput,
    ToolExecutor,
    run_insights,
)
from app.analysis.edu import (
    AssessmentRecord,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    ScorePolicy,
    prepare_dataset,
)


@pytest.fixture
def reviewed_budget_clock(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    day = getattr(request, "param", date(2026, 11, 19))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return cls(day.year, day.month, day.day, tzinfo=tz)

    # Keep the production authorization path intact and replace only its wall clock.
    monkeypatch.setattr(live_budget, "datetime", FixedDateTime)


def call(name="get_dimension_statistics", **arguments):
    return {"name": name, "arguments": {"batch_id": "synthetic-batch", **arguments}}


def proposal(prompt):
    view = json.loads(prompt)
    chosen = [
        item["fact_id"] for item in view["facts"] if item["metric"] in {"mean", "matched_ratio"}
    ][:1]
    if not chosen:
        chosen = [view["facts"][0]["fact_id"]]
    evidence = view["evidence_ids"][:2]
    return {
        "observed_facts": [{"fact_id": identifier} for identifier in chosen],
        "hypotheses": [
            {
                "kind": "practice_opportunity",
                "status": "hypothesis",
                "fact_ids": chosen,
                "evidence_ids": evidence,
            }
        ],
        "teaching_actions": [
            {
                "kind": "guided_practice",
                "fact_ids": chosen,
                "evidence_ids": [],
            }
        ],
        "verification_steps": [
            {
                "kind": "collect_work_samples",
                "fact_ids": chosen,
                "evidence_ids": [],
            }
        ],
        "limitations": [],
        "cited_fact_ids": list(chosen),
        "cited_evidence_ids": evidence,
    }


@dataclass
class FakeLLM:
    calls: list = field(default_factory=lambda: [call()])
    response: object = proposal
    offline_only: bool = True
    prompts: list[str] = field(default_factory=list, repr=False)
    phase_error: str | None = None
    delay_phase: str | None = None
    compose_count: int = 0

    async def plan(self, prompt):
        self.prompts.append(prompt)
        if self.delay_phase == "plan":
            await asyncio.sleep(10)
        if self.phase_error == "plan":
            raise RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION_DO_NOT_ECHO")
        return json.dumps({"calls": self.calls})

    async def compose(self, prompt):
        self.prompts.append(prompt)
        self.compose_count += 1
        if self.delay_phase == "compose":
            await asyncio.sleep(10)
        if self.phase_error == "compose":
            raise RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION_DO_NOT_ECHO")
        result = self.response(prompt) if callable(self.response) else self.response
        return result if isinstance(result, str) else json.dumps(result)


@dataclass
class MemoryProvider:
    documents: dict = field(repr=False)
    reads: list = field(default_factory=list, repr=False)

    def get(self, access, evidence_id):
        self.reads.append((access.batch_id, evidence_id))
        return self.documents.get(evidence_id)


@dataclass
class Scenario:
    source: SyntheticInput
    evidence: MemoryProvider
    rubric: MemoryProvider

    def executor(self, **kwargs):
        return ToolExecutor(
            self.source.dataset, self.source.access, self.evidence, self.rubric, **kwargs
        )

    def run(self, client=None, **kwargs):
        return asyncio.run(
            run_insights(
                "合成问题：请基于本批数据提出待验证教学建议。",
                self.source,
                client or FakeLLM(),
                self.evidence,
                self.rubric,
                **kwargs,
            )
        )


@pytest.fixture
def scenario():
    rows = []
    for index, (algorithm, efficiency, strategy) in enumerate(
        [(2, 2, 4), (2.5, 1.5, 5), (3, 3, 3), (4, 2, 4.5)]
    ):
        refs = (
            EvidenceRef(
                "synthetic-source", f"row-{index}/score", EvidenceKind.SCORE, Dimension.ALGORITHMS
            ),
            EvidenceRef(
                "synthetic-source",
                f"row-{index}/comment",
                EvidenceKind.COMMENT,
                Dimension.ALGORITHMS,
            ),
        )
        scores = {dimension: 3 for dimension in Dimension}
        scores.update(
            {
                Dimension.ALGORITHMS: algorithm,
                Dimension.EFFICIENCY: efficiency,
                Dimension.STRATEGY: strategy,
            }
        )
        rows.append(
            AssessmentRecord(
                "synthetic-batch",
                f"synthetic-student-{index}",
                f"synthetic-row-{index}",
                "synthetic-source",
                scores,
                refs,
            )
        )
    rows.append(
        AssessmentRecord(
            "synthetic-other-batch",
            "synthetic-student-0",
            "synthetic-other-row",
            "synthetic-other-source",
            {dimension: 5 for dimension in Dimension},
        )
    )
    dataset = prepare_dataset(rows, policy=ScorePolicy())
    selected = tuple(
        row.reference for row in dataset.records if row.reference.batch_id == "synthetic-batch"
    )
    grants = tuple(
        EvidenceGrant(evidence_id=name, record=selected[0], reference=ref)
        for name, ref in zip(
            ("score-alpha", "comment-alpha"), selected[0].evidence_refs, strict=True
        )
    )
    rubrics = tuple(
        RubricGrant(
            evidence_id=f"rubric-{dimension.name}",
            batch_id="synthetic-batch",
            dimension=dimension,
            version="synthetic-rubric-draft",
            reference=EvidenceRef(
                "synthetic-rubric-source",
                f"synthetic-criteria/{dimension.name}",
                EvidenceKind.BASIS,
                dimension,
            ),
        )
        for dimension in Dimension
    )
    access = SyntheticAccess(
        batch_id="synthetic-batch",
        student_ids=tuple(item.student_id for item in selected),
        evidence_grants=grants,
        rubric_grants=rubrics,
    )
    evidence = MemoryProvider(
        {
            grant.evidence_id: EvidenceDocument(
                grant,
                "合成评语：讨论算法；本句仅为测试，不代表真实学生。",
            )
            for grant in grants
        }
    )
    rubric = MemoryProvider(
        {
            grant.evidence_id: RubricDocument(
                grant,
                "合成评价依据：仅用于离线测试，不是正式教师量表。",
            )
            for grant in rubrics
        }
    )
    return Scenario(SyntheticInput(dataset, access), evidence, rubric)
