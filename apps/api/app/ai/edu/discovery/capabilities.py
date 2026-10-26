"""Trusted capability selection seam; V1 schemas and the executor remain authoritative."""

from dataclasses import dataclass
from typing import Protocol

from .models import SCHEMAS, AgentError, Strict


class CapabilityRegistry(Protocol):
    def authorize(self, tool: str, arguments: Strict) -> None:
        """Restrict an already typed tool to capabilities available in this assessment."""
        ...


@dataclass(frozen=True)
class RegisteredCapabilities:
    # A future reviewed AssessmentItem adapter can select these without changing graph edges.
    # This does not register a new parser, schema, scale or executable tool implementation.
    tools: frozenset[str] = frozenset(SCHEMAS) - {"finish"}
    dimensions: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tools, frozenset) or not self.tools <= SCHEMAS.keys() - {"finish"}:
            raise AgentError("invalid_capability_registry")
        if self.dimensions is not None and (
            not isinstance(self.dimensions, frozenset)
            or any(not isinstance(dimension, str) for dimension in self.dimensions)
        ):
            raise AgentError("invalid_capability_registry")

    def authorize(self, tool: str, arguments: Strict) -> None:
        if tool not in self.tools:
            raise AgentError("tool_not_available")
        if self.dimensions is None:
            return
        used = set()
        for name in ("left", "right", "varying_dimension"):
            if hasattr(arguments, name):
                used.add(getattr(arguments, name))
        used.update(getattr(arguments, "outcomes", ()))
        used.update(predicate.dimension for predicate in getattr(arguments, "conditions", ()))
        if not used <= self.dimensions:
            raise AgentError("tool_not_available")
