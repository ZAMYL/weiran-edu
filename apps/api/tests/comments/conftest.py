from collections.abc import Callable

import pytest

from app.analysis.edu import (
    AssessmentRecord,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    ScorePolicy,
    prepare_dataset,
)
from app.analysis.edu.comments import CommentInput
from app.analysis.edu.models import RawScore


@pytest.fixture
def make_case() -> Callable[..., tuple[AssessmentRecord, CommentInput]]:
    def make(
        text: str | None,
        *,
        student: str = "synthetic-001",
        batch: str = "synthetic-batch",
        score: RawScore = 3,
        dimension: Dimension = Dimension.ALGORITHMS,
        score_refs: bool = True,
        comment_dimension: Dimension | None = None,
    ) -> tuple[AssessmentRecord, CommentInput]:
        score_evidence = (
            tuple(
                EvidenceRef("synthetic-scores", f"{student}/{item.name}", EvidenceKind.SCORE, item)
                for item in Dimension
            )
            if score_refs
            else ()
        )
        comment_evidence = EvidenceRef(
            "synthetic-comments", f"{student}/comment-main", EvidenceKind.COMMENT, comment_dimension
        )
        extra = EvidenceRef("synthetic-comments", f"{student}/comment-extra", EvidenceKind.COMMENT)
        row = AssessmentRecord(
            batch,
            student,
            f"row-{student}",
            "synthetic-scores",
            {item: score if item is dimension else 3 for item in Dimension},
            (*score_evidence, comment_evidence, extra),
        )
        reference = prepare_dataset([row], policy=ScorePolicy()).records[0].reference
        return row, CommentInput(reference, f"comment-{student}", comment_evidence, text)

    return make
