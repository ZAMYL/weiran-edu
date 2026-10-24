"""Deterministic discovery and internal aggregate projection; no AI or public routes."""

from .discovery import discover_candidates, metric_sources
from .models import DiscoveryPolicy, DistributionFocus, GroupRule, InsightCandidate
from .projection import public_cards, teacher_cards

__all__ = [
    "DiscoveryPolicy",
    "DistributionFocus",
    "GroupRule",
    "InsightCandidate",
    "discover_candidates",
    "metric_sources",
    "public_cards",
    "teacher_cards",
]
