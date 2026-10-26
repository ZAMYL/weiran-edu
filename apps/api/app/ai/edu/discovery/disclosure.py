"""Identical cumulative disclosure policy for legacy and prototype runs."""

from collections.abc import Hashable, Iterable, Sequence

from .models import AgentError


def check_disclosure(
    sets: Sequence[Iterable[Hashable]],
    *,
    released: list[frozenset],
    staged: list[frozenset],
    minimum_cell: int,
) -> list[frozenset]:
    pending = []
    for records in sets:
        group = frozenset(records)
        if group and len(group) < minimum_cell:
            raise AgentError("privacy_limited")
        if any(0 < len(group ^ old) < minimum_cell for old in released + staged + pending):
            raise AgentError("privacy_limited")
        pending.append(group)
    return pending
