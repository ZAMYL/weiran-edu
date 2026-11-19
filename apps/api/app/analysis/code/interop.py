"""Adapters between this kernel and the B-01 / C-01 contracts.

This module is deliberately free of imports from ``app.edu`` or ``prototypes``:
those live on unmerged PRs (#105, #107) and are not part of this branch. The
mapping functions operate on plain strings and dataclasses so the real provider
can call them and copy the results into its own DTOs / ``Witness`` objects.

Three gaps are bridged here:

* B-01 stores verdicts and test-point status as **Chinese words** (``答案正确``,
  ``运行超时``, ...) and language as a free-form **compiler label** (``GXX``).
* C-01's ``FeatureCode`` whitelist has only five codes; this kernel extracts a
  richer feature set, so only the whitelisted subset is projected.
* C-01's ``FeatureAssociation`` is a per-cohort ``checked``/``present`` report,
  not a 2x2 contingency; :func:`c01_feature_associations` derives that shape.
"""

from dataclasses import dataclass

from .contract import CodeState, Language, TestPointOutcome, Verdict
from .features import FeatureId, is_present
from .results import Analysis

# B-01 answer-sheet status words -> this kernel's verdicts. The words are the
# canonical values emitted by ``importing/edu/answersheet.py`` (``_STATUS_WORDS``,
# including the runtime family added at B-01 ``a0d1422``). The runtime family
# (``运行时错误`` / ``段错误`` / ``非零返回``) shares one meaning with ``运行错误``:
# a runtime failure. This is the same mapping PR #112's acceptance script applied
# via its private ``normalize_status`` shim, now owned by this formal adapter so
# integrations no longer need a per-consumer shim. Unlisted text stays ``UNKNOWN``
# and is never reclassified as compile/runtime failure or success.
_B01_VERDICT: dict[str, Verdict] = {
    "答案正确": Verdict.ACCEPTED,
    "答案错误": Verdict.WRONG_ANSWER,
    "运行超时": Verdict.TIME_LIMIT_EXCEEDED,
    "内存超限": Verdict.MEMORY_LIMIT_EXCEEDED,
    "运行错误": Verdict.RUNTIME_ERROR,
    "运行时错误": Verdict.RUNTIME_ERROR,
    "段错误": Verdict.RUNTIME_ERROR,
    "非零返回": Verdict.RUNTIME_ERROR,
    "格式错误": Verdict.FORMAT_ERROR,
    "编译错误": Verdict.COMPILE_ERROR,
    "部分正确": Verdict.PARTIAL,
    "未作答": Verdict.NOT_ATTEMPTED,
}

# Status words that mean a clean per-test-point failure (the rest are ambiguous
# at the test-point level, e.g. "编译错误" is a whole-submission outcome).
_B01_TESTPOINT_FAILURES = frozenset(
    {
        "答案错误",
        "运行超时",
        "内存超限",
        "运行错误",
        "运行时错误",
        "段错误",
        "非零返回",
        "格式错误",
    }
)

# This kernel's verdicts -> C-01 status strings (``programming_evidence.py``).
_VERDICT_TO_C01: dict[Verdict, str] = {
    Verdict.ACCEPTED: "accepted",
    Verdict.WRONG_ANSWER: "wrong_answer",
    Verdict.RUNTIME_ERROR: "runtime_error",
    Verdict.TIME_LIMIT_EXCEEDED: "timeout",
    Verdict.MEMORY_LIMIT_EXCEEDED: "memory_limit",
    Verdict.FORMAT_ERROR: "format_error",
    Verdict.COMPILE_ERROR: "compile_error",
    Verdict.PARTIAL: "partial",
    Verdict.NOT_ATTEMPTED: "not_attempted",
    Verdict.UNKNOWN: "unknown",
}

# This kernel's feature -> C-01 ``FeatureCode`` whitelist. Features not listed
# here (max_loop_nesting, functions, container_uses) remain available through
# ``Analysis`` but are not projected into C-01's fixed vocabulary.
_C01_FEATURE_CODE: dict[FeatureId, str] = {
    FeatureId.BRANCHES: "boundary_check",
    FeatureId.LOOPS: "loop",
    FeatureId.DIRECT_SELF_RECURSION: "recursion",
    FeatureId.INDEX_ACCESSES: "indexing",
    FeatureId.IO_OPERATIONS: "input_parsing",
}


def b01_verdict(status: str | None) -> Verdict:
    """Map a B-01 status word to a typed verdict; unknown text stays ``UNKNOWN``."""

    if status is None:
        return Verdict.UNKNOWN
    return _B01_VERDICT.get(status, Verdict.UNKNOWN)


def b01_test_point_outcome(status: str | None) -> TestPointOutcome:
    """Map a B-01 test-point status word to a typed outcome.

    ``答案正确`` is the only pass; the clear failure words map to ``FAILED`` and
    everything else (``编译错误``, ``部分正确``, ``未作答``, absent) to ``UNKNOWN``.
    """

    if status is None:
        return TestPointOutcome.UNKNOWN
    if status == "答案正确":
        return TestPointOutcome.PASSED
    if status in _B01_TESTPOINT_FAILURES:
        return TestPointOutcome.FAILED
    return TestPointOutcome.UNKNOWN


def b01_language(label: str | None) -> Language | None:
    """Map a B-01 compiler label (``编译器: GXX``) to a supported language.

    Returns ``None`` when the label is absent or ambiguous (for example ``GCC``,
    which does not distinguish C from C++).
    """

    if not label:
        return None
    normalized = label.strip().lower()
    if any(token in normalized for token in ("gxx", "g++", "c++", "cpp")):
        return Language.CPP
    if any(token in normalized for token in ("python", "pypy", "python3")):
        return Language.PYTHON
    return None


def b01_code_state(state: str | None) -> CodeState:
    """Map a B-01 code-state word to a typed state; unknown text stays ``MISSING``.

    ``MISSING`` is the conservative fallback: when the state cannot be confirmed as
    ``PRESENT``, features must be ``UNKNOWN`` rather than extracted from a possible
    placeholder. B-01's canonical values are ``PRESENT`` / ``UNANSWERED`` / ``MISSING``.
    """

    if state == "PRESENT":
        return CodeState.PRESENT
    if state == "UNANSWERED":
        return CodeState.UNANSWERED
    if state == "MISSING":
        return CodeState.MISSING
    return CodeState.MISSING


def c01_status(verdict: Verdict) -> str:
    return _VERDICT_TO_C01[verdict]


def c01_feature_code(feature: FeatureId) -> str | None:
    return _C01_FEATURE_CODE.get(feature)


@dataclass(frozen=True)
class C01FeatureAssociation:
    """DS-03 result for one failure cohort, shaped like C-01's ``FeatureAssociation``.

    ``present`` pairs a participant id with a stable per-submission reference
    (the submission's ``source.location``); the C-01 provider maps that reference
    to its own ``Witness``. This class never inspects code to derive a value.
    """

    problem: str
    point: str | None
    status: str
    code: str
    checked: tuple[str, ...]
    present: tuple[tuple[str, str], ...]


def c01_feature_associations(
    analysis: Analysis,
    *,
    status: str,
    point: str | None = None,
) -> tuple[C01FeatureAssociation, ...]:
    """Project one failure cohort of ``analysis`` into C-01's feature shape.

    The cohort is every submission whose C-01 status equals ``status`` (and, when
    ``point`` is given, that also failed that test point). For each whitelisted
    feature, ``checked`` lists cohort participants with a known feature value and
    ``present`` lists those where the feature is present.
    """

    cohort = [
        submission
        for submission in analysis.submissions
        if c01_status(submission.verdict) == status
        and (
            point is None
            or any(
                test.testpoint_id == point and test.outcome is TestPointOutcome.FAILED
                for test in submission.test_points
            )
        )
    ]
    associations = []
    for feature in FeatureId:
        code = c01_feature_code(feature)
        if code is None:
            continue
        checked = tuple(
            submission.reference.student_id
            for submission in cohort
            if is_present(submission.features, feature) is not None
        )
        present = tuple(
            (submission.reference.student_id, submission.reference.source.location)
            for submission in cohort
            if is_present(submission.features, feature) is True
        )
        associations.append(
            C01FeatureAssociation(analysis.problem_id, point, status, code, checked, present)
        )
    return tuple(associations)
