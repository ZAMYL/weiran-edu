"""Three-level explanation boundary.

The kernel distinguishes, structurally, three kinds of statement:

1. :class:`ObservedFact`          -- deterministic and reproducible: a feature
   count, a submitted program's verdict, a test-point failure.
2. :class:`StatisticalAssociation` -- a measured co-occurrence with its exact
   sample sizes and proportions.
3. :class:`Hypothesis`            -- an unverified interpretation.

The third level is never auto-promoted to a confirmed root cause: :class:`Hypothesis`
carries a ``status`` whose only value is ``UNVERIFIED``. Code style is never used to
infer a student's character, understanding, tool use, or a five-dimension score.
"""

from dataclasses import dataclass
from enum import Enum

from .association import Contingency
from .contract import SourceRef


class HypothesisStatus(Enum):
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ObservedFact:
    statement: str
    evidence: tuple[SourceRef, ...] = ()


@dataclass(frozen=True)
class StatisticalAssociation:
    statement: str
    contingency: Contingency


@dataclass(frozen=True)
class Hypothesis:
    claim: str
    status: HypothesisStatus = HypothesisStatus.UNVERIFIED
