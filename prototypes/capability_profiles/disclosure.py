"""Profile-only refinement of the shared cumulative disclosure gate."""

from app.ai.edu.discovery.disclosure import check_disclosure
from app.ai.edu.discovery.models import AgentError


def check_profile_disclosure(sets, *, released, staged, minimum_cell):
    checked = check_disclosure(sets, released=released, staged=staged, minimum_cell=minimum_cell)
    history = list(dict.fromkeys(frozenset(s) for s in [*released, *staged, *checked] if s))
    # Counts for overlapping sets disclose intersections AND relative complements.
    # Refinement also catches cells induced by three or more published partitions.
    atoms = [frozenset().union(*history)] if history else []
    for group in history:
        atoms = [part for atom in atoms for part in (atom & group, atom - group) if part]
        if any(len(atom) < minimum_cell for atom in atoms):
            raise AgentError("privacy_limited")
    return checked
