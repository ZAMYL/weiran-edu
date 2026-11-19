"""Language extractor registry.

Only languages with a real extractor are exposed; an unknown language yields an
all-``UNKNOWN`` feature set rather than a guess.
"""

from ..contract import Language
from ..features import FeatureSet, ParseStatus, unknown_set
from .cpp import CppExtractor
from .python import PythonExtractor

_EXTRACTORS = {
    Language.PYTHON: PythonExtractor(),
    Language.CPP: CppExtractor(),
}

__all__ = ["CppExtractor", "PythonExtractor", "extractor_for"]


def extractor_for(language: Language | None):
    """Return the extractor for ``language`` (``None`` when unsupported)."""

    if language is None:
        return None
    return _EXTRACTORS.get(language)


def extract(language: Language | None, source_code: str) -> FeatureSet:
    """Extract features, or an all-``UNKNOWN`` set for an undetermined/unsupported language."""

    if language is None:
        return unknown_set(None, ParseStatus.UNKNOWN_LANGUAGE, "language_undetermined")
    extractor = _EXTRACTORS.get(language)
    if extractor is None:
        return unknown_set(language, ParseStatus.UNSUPPORTED_LANGUAGE)
    return extractor.extract(source_code)
