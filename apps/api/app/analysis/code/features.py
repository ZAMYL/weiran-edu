"""Feature schema shared by every language extractor.

Every extractor returns a :class:`FeatureSet` with one :class:`FeatureValue` per
:class:`FeatureId`. A feature that cannot be determined reliably for a given
language or input is reported as ``UNKNOWN`` rather than guessed, so a consumer
can always tell a measured fact from a placeholder.

Confidence levels
-----------------
* ``RELIABLE``   -- deterministic given a successful full parse (e.g. Python AST).
* ``TENTATIVE``  -- deterministic given a successful lexical/structural parse
  (e.g. the C++ lexer), but without full semantic analysis.
* ``UNKNOWN``    -- cannot be determined reliably for this language or input.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .contract import Language


class FeatureId(Enum):
    """Structural features extracted from source code.

    * ``branches``          -- count of branch sites (``if``, ternary, ``match``/``switch``).
    * ``loops``             -- count of loop constructs (``for``/``while``/``do``).
    * ``max_loop_nesting``  -- deepest loop-in-loop depth (0 when there are no loops).
    * ``functions``         -- count of function/method/lambda definitions.
    * ``direct_self_recursion`` -- whether a function directly calls itself.
    * ``index_accesses``    -- count of subscript/index access sites.
    * ``container_uses``    -- count of container literal/type constructions.
    * ``io_operations``     -- count of input/output call sites.
    """

    BRANCHES = "branches"
    LOOPS = "loops"
    MAX_LOOP_NESTING = "max_loop_nesting"
    FUNCTIONS = "functions"
    DIRECT_SELF_RECURSION = "direct_self_recursion"
    INDEX_ACCESSES = "index_accesses"
    CONTAINER_USES = "container_uses"
    IO_OPERATIONS = "io_operations"


class Confidence(Enum):
    RELIABLE = "reliable"
    TENTATIVE = "tentative"
    UNKNOWN = "unknown"


class ParseStatus(Enum):
    OK = "ok"
    MISSING_SOURCE = "missing_source"
    UNPARSEABLE = "unparseable"
    UNSUPPORTED_LANGUAGE = "unsupported_language"


@dataclass(frozen=True)
class FeatureValue:
    feature: FeatureId
    value: int | bool | None
    confidence: Confidence
    reason: str | None = None


@dataclass(frozen=True)
class FeatureSet:
    language: Language
    status: ParseStatus
    detail: str | None
    values: tuple[FeatureValue, ...]

    def get(self, feature: FeatureId) -> FeatureValue:
        return next(value for value in self.values if value.feature is feature)


class FeatureExtractor(Protocol):
    """A per-language extractor. ``extract`` must never raise on source text."""

    language: Language

    def extract(self, source_code: str) -> FeatureSet: ...


def unknown_set(language: Language, status: ParseStatus, detail: str | None = None) -> FeatureSet:
    """An all-``UNKNOWN`` feature set for missing/unparseable/unsupported input."""

    values = tuple(FeatureValue(feature, None, Confidence.UNKNOWN, detail) for feature in FeatureId)
    return FeatureSet(language, status, detail, values)


def presence_threshold(feature: FeatureId) -> int:
    """Binarisation threshold for a count feature when asking "is it present?".

    ``max_loop_nesting`` uses 2 so its presence means "has *nested* loops" rather
    than the redundant "has at least one loop" (which ``loops`` already captures).
    """

    return 2 if feature is FeatureId.MAX_LOOP_NESTING else 1


def is_present(values: FeatureSet, feature: FeatureId) -> bool | None:
    """Whether ``feature`` is present; ``None`` when the value is unknown."""

    value = values.get(feature).value
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return value >= presence_threshold(feature)
