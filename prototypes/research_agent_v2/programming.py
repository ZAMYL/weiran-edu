"""C-01 programming investigations using the existing ResearchExecutor and session."""

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Literal, get_args
from uuid import uuid4

import httpx
from app.ai.edu.discovery.models import (
    AgentError,
    AgentRun,
    FactId,
    FinishArgs,
    Limits,
    Strict,
)
from app.ai.edu.discovery.session import _run_session
from pydantic import Field, ValidationError

from prototypes.adaptive_assessment.analysis import MetricSource
from prototypes.adaptive_assessment.live.budget import CampaignBudget, OfflineProgrammingPermit
from prototypes.adaptive_assessment.live.planner import QwenAdaptivePlanner

from .programming_evidence import (
    FeatureCode,
    FindingStatus,
    ProgrammingSource,
    SubmissionEvidence,
)
from .runtime import ResearchExecutor

OVERVIEW = "get_programming_overview"
INSPECT = "inspect_failure_evidence"
_FAILURES = frozenset(get_args(FindingStatus))
_TEST_STATUSES = _FAILURES | {"accepted", "unknown"}
_VERDICTS = _TEST_STATUSES | {"not_attempted", "compile_error", "partial"}
_FEATURES = frozenset(get_args(FeatureCode))
_SHA = re.compile(r"[a-f0-9]{64}")
_CHECKS = {
    "wrong_answer": "核对题意、边界输入和输出；用源代码复查，不推断隐藏输入。",
    "timeout": "复查循环终止条件与复杂度；在同题同测试点核对运行环境。",
    "memory_limit": "复查数据规模、存储结构与内存分配。",
    "runtime_error": "复查索引、输入约束和运行诊断。",
    "format_error": "逐项核对题目的输出格式。",
    "compile_error": "查看原编译诊断，复查语法、类型、编译器和依赖。",
    "other_failure": "先核对原始评测诊断和状态。",
}


class OverviewArgs(Strict):
    pass  # The server has already bound the teacher's requested scope.


class InspectArgs(Strict):
    overview_id: FactId
    direction: str = Field(pattern=r"^D[1-9][0-9]{0,2}$")


SCHEMAS = {OVERVIEW: OverviewArgs, INSPECT: InspectArgs, "finish": FinishArgs}


class ProgrammingDecision(Strict):
    name: Literal["get_programming_overview", "inspect_failure_evidence", "finish"]
    arguments: dict[str, object]
    basis_fact_ids: list[FactId] = Field(default_factory=list, max_length=8)


def scope_for_question(question: str, source: ProgrammingSource) -> tuple[str, ...]:
    """Resolve a bounded teacher command locally; no free text enters native dialogue."""
    source.validate()
    if not isinstance(question, str) or not 1 <= len(question) <= 300:
        raise AgentError("unsupported_programming_question")
    request = question.strip()
    if request.rstrip("。？！?!") == "调查编程错误模式":
        return source.material.problems
    prefix, separator, label = request.replace("：", ":", 1).partition(":")
    if separator and prefix.strip() == "调查题目" and label.strip() in source.material.problems:
        return (label.strip(),)
    raise AgentError("unsupported_programming_question")


@dataclass(frozen=True)
class _Direction:
    problem: str
    point: str | None
    status: str
    covered: tuple[tuple[str, str], ...]
    affected: tuple[tuple[str, str], ...]


class _ProgrammingKernel:
    def __init__(self, source: ProgrammingSource, problems: tuple[str, ...]):
        self.material = source.material
        self.participants = self.material.participants
        self.witnesses = {w.reference_id: w for w in self.material.witnesses}
        if (
            not _SHA.fullmatch(source.fingerprint)
            or len(set(self.participants)) != len(self.participants)
            or len(set(self.material.problems)) != len(self.material.problems)
            or len(self.witnesses) != len(self.material.witnesses)
        ):
            raise AgentError("invalid_programming_snapshot")
        if (
            not problems
            or len(set(problems)) != len(problems)
            or not set(problems) <= set(self.material.problems)
        ):
            raise AgentError("goal_item_outside_scope")
        known_people, known_problems = set(self.participants), set(self.material.problems)
        self.submissions: dict[tuple[str, str], SubmissionEvidence] = {}
        for row in self.material.submissions:
            subject = (row.participant, row.problem)
            if (
                subject in self.submissions
                or row.participant not in known_people
                or row.problem not in known_problems
                or row.verdict not in _VERDICTS
                or len({test.point for test in row.tests}) != len(row.tests)
                or (row.verdict == "not_attempted" and (row.tests or row.code_ref))
            ):
                raise AgentError("invalid_programming_snapshot")
            self.submissions[subject] = row
            self.witness(row.verdict_ref, *subject, "verdict", row.verdict)
            if row.code_ref:
                self.witness(row.code_ref, *subject, "code")
            for test in row.tests:
                if not test.point or test.status not in _TEST_STATUSES:
                    raise AgentError("invalid_programming_snapshot")
                self.witness(test.ref, *subject, "test", json.dumps([test.point, test.status]))
        self.problems = {f"P{i}": problem for i, problem in enumerate(problems, 1)}
        self.points: dict[str, dict[str, str]] = {}
        self.rows: dict[str, list[SubmissionEvidence]] = defaultdict(list)
        for row in self.submissions.values():
            self.rows[row.problem].append(row)
        for problem in problems:
            points = sorted({t.point for row in self.rows[problem] for t in row.tests})
            self.points[problem] = {point: f"T{i}" for i, point in enumerate(points, 1)}

    def witness(self, ref: str, pid: str, problem: str, kind: str, value: str | None = None):
        witness = self.witnesses.get(ref)
        if (
            witness is None
            or witness.participant_id != pid
            or witness.item_id != problem
            or witness.kind != kind
            or (value is not None and witness.value != value)
            or not witness.source_id
            or not witness.locator
            or not all(
                _SHA.fullmatch(value)
                for value in (witness.source_sha256, witness.entry_sha256, witness.content_sha256)
            )
        ):
            raise AgentError("source_scope_mismatch")
        return witness

    def catalog(self, minimum_cell: int) -> dict:
        return {
            "goal": "programming_failure_patterns",
            "problems": list(self.problems),
            "minimum_cell": minimum_cell,
        }

    def candidates(self, problem: str) -> list[_Direction]:
        rows = self.rows[problem]
        covered: dict[str, list[tuple[str, str]]] = defaultdict(list)
        failed: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        attempted, without_tests = [], defaultdict(list)
        for row in rows:
            if row.verdict not in {"not_attempted", "unknown"}:
                attempted.append((row.participant, row.verdict_ref))
                if not row.tests and row.verdict in _FAILURES | {"compile_error"}:
                    without_tests[row.verdict].append((row.participant, row.verdict_ref))
            for test in row.tests:
                if test.status == "unknown":
                    continue  # A missing/uninterpretable verdict is not a measured failure.
                covered[test.point].append((row.participant, test.ref))
                if test.status in _FAILURES:
                    failed[test.point, test.status].append((row.participant, test.ref))
        directions = [
            _Direction(problem, point, status, tuple(covered[point]), tuple(refs))
            for (point, status), refs in sorted(failed.items())
        ]
        return directions + [
            _Direction(problem, None, status, tuple(attempted), tuple(refs))
            for status, refs in sorted(without_tests.items())
        ]


def _metric(key: str, problem: str, pairs: tuple[tuple[str, str], ...]) -> MetricSource:
    return MetricSource(key, problem, tuple(p for p, _ in pairs), tuple(r for _, r in pairs))


class ProgrammingExecutor(ResearchExecutor):
    """Only the domain projections change; commits, checks and replay stay shared."""

    def __init__(
        self, source: ProgrammingSource, run_id: str, limits: Limits, problems: tuple[str, ...]
    ):
        source.validate()
        self.source, self.run_id, self.limits = source, run_id, limits
        self.kernel = _ProgrammingKernel(source, problems)
        self.facts, self.directions = {}, {}
        self.released = [frozenset(self.kernel.participants)]
        self.seen = set()
        self.output_bytes = self.compute_seconds = 0
        self.attempt_ledger = []
        self._verified_ledger = None

    def _ledger_key(self):
        return (super()._ledger_key(), tuple(self.directions.items()))

    def _verify_replayed_state(self, replay):
        if replay.directions != self.directions:
            raise AgentError("source_validation_failed")

    def _replay_executor(self):
        return ProgrammingExecutor(
            self.source, self.run_id, self.limits, tuple(self.kernel.problems.values())
        )

    def _witness_in_snapshot(self, witness):
        # Capture verifies the complete bound snapshot; each Witness retains the
        # original file digest, which need not equal the assessment snapshot digest.
        return witness == self.kernel.witnesses.get(witness.reference_id)

    def parse(self, name, arguments):
        try:
            return SCHEMAS[name].model_validate(arguments)
        except (KeyError, ValidationError):
            raise AgentError("invalid_tool_arguments") from None

    def _permit(self, sources, staged):
        try:
            staged.extend(self._gate(sources, staged))
        except AgentError as failure:
            if str(failure) != "privacy_limited":
                raise
            return False
        return True

    def _overview(self):
        metrics, sources, staged, candidates = [], [], [], []
        aliases = {problem: alias for alias, problem in self.kernel.problems.items()}
        limited = False
        for alias, problem in self.kernel.problems.items():
            rows = self.kernel.rows[problem]
            by_status = defaultdict(list)
            for row in rows:
                by_status[row.verdict].append((row.participant, row.verdict_ref))
            groups = [
                _metric(f"{alias}.verdict.{status}", problem, tuple(pairs))
                for status, pairs in sorted(by_status.items())
            ]
            observed = _metric(
                f"{alias}.observed", problem, tuple((r.participant, r.verdict_ref) for r in rows)
            )
            if not rows or not self._permit([observed, *groups], staged):
                # Test coverage could expose complements of verdict cells withheld above.
                limited = True
                metrics.append({"problem": alias, "state": "unavailable_or_privacy_limited"})
                continue
            sources.extend([observed, *groups])
            metrics.append(
                {
                    "problem": alias,
                    "state": "available",
                    "participants_in_scope": len(self.kernel.participants),
                    "verdict_records": len(rows),
                    "verdict_counts": dict(sorted(Counter(r.verdict for r in rows).items())),
                }
            )
            candidates.extend(self.kernel.candidates(problem))
        directions = []
        admitted = {}
        for d in sorted(
            candidates,
            key=lambda d: (-len(d.affected), aliases[d.problem], d.point or "", d.status),
        ):
            if len(directions) == 12:
                limited = True
                break
            key = f"D{len(directions) + 1}"
            proof = (
                _metric(f"{key}.coverage", d.problem, d.covered),
                _metric(f"{key}.affected", d.problem, d.affected),
            )
            if not self._permit(proof, staged):
                limited = True
                continue
            sources.extend(proof)
            admitted[key] = d
            directions.append(
                {
                    "direction": key,
                    "problem": aliases[d.problem],
                    "test": self.kernel.points[d.problem].get(d.point),
                    "phenomenon": d.status,
                    "affected": len(d.affected),
                    "covered": len(d.covered),
                    "denominator": "observed_test_verdicts" if d.point else "known_attempts",
                }
            )
        return (
            {
                "items": metrics,
                "directions": directions,
                "limited": limited,
            },
            tuple(sources),
            staged,
            admitted,
        )

    def _inspect(self, args):
        overview = self.facts.get(args.overview_id)
        if overview is None or overview.tool != OVERVIEW or args.direction not in self.directions:
            raise AgentError("direction_not_in_verified_overview")
        self._verify(overview)
        d = self.directions[args.direction]
        summary = next(x for x in overview.view["directions"] if x["direction"] == args.direction)
        prefix = args.direction + "."
        sources = [s for s in overview.item_sources if s.key.startswith(prefix)]
        staged = []
        affected = frozenset(p for p, _ in d.affected)
        has_code = tuple(
            (pid, ref)
            for pid in sorted(affected)
            if (ref := self.kernel.submissions[pid, d.problem].code_ref)
        )
        code_proof = _metric("code.available", d.problem, has_code)
        code_count = None
        if self._permit([code_proof], staged):
            code_count = len(has_code)
            if has_code:
                sources.append(code_proof)

        associations = []
        feature_limited = False
        seen_features = set()
        for feature in self.source.material.features:
            if (feature.problem, feature.point, feature.status) != (d.problem, d.point, d.status):
                continue
            checked = frozenset(feature.checked)
            present = frozenset(p for p, _ in feature.present)
            if (
                feature.code not in _FEATURES
                or feature.code in seen_features
                or len(checked) != len(feature.checked)
                or len(present) != len(feature.present)
                or not present <= checked <= {pid for pid, _ in has_code}
            ):
                raise AgentError("invalid_feature_association")
            seen_features.add(feature.code)
            for pid, ref in feature.present:
                self.kernel.witness(ref, pid, d.problem, "feature", feature.code)
            feature_sources = (
                _metric(
                    f"{feature.code}.checked",
                    d.problem,
                    tuple(x for x in has_code if x[0] in checked),
                ),
                _metric(f"{feature.code}.present", d.problem, feature.present),
            )
            if not checked or not self._permit(feature_sources, staged):
                feature_limited = True
                continue
            sources.extend(feature_sources)
            associations.append(
                {"feature": feature.code, "checked": len(checked), "present": len(present)}
            )
        limits = ["cause_unconfirmed"]
        if d.point is None:
            limits.append("test_evidence_missing")
        if code_count != len(affected):
            limits.append("code_unavailable_or_withheld")
        if not associations or feature_limited:
            limits.append("features_unavailable_or_withheld")
        return (
            {
                "qualified": True,
                "basis_fact_id": overview.fact_id,
                "direction": summary,
                "affected_with_code": code_count,
                "features": associations,
                "limitations": limits,
            },
            tuple(sources),
            staged,
        )

    def _execute(self, name, args):
        query = {"tool": name, "arguments": args.model_dump()}
        key = json.dumps(query, sort_keys=True)
        if key in self.seen:
            raise AgentError("repeated_tool")
        self.seen.add(key)
        self.source.validate()
        if name == OVERVIEW:
            projection, sources, staged, admitted = self._overview()
            facts = self._commit_fact(name, query, projection, sources, staged)
            self.directions = admitted
            return facts
        if name != INSPECT:
            raise AgentError("unavailable_tool")
        projection, sources, staged = self._inspect(args)
        return self._commit_fact(name, query, projection, sources, staged)

    def finish(self, args):
        if len(args.selected_fact_ids) != len(set(args.selected_fact_ids)) or bool(
            args.selected_fact_ids
        ) != (args.reason == "completed"):
            raise AgentError("inconsistent_stop_reason")
        self.verify_all()
        if not self.facts:
            raise AgentError("overview_required")
        overview = next(iter(self.facts.values()))
        if not args.selected_fact_ids and (
            any(f.tool == INSPECT for f in self.facts.values())
            or (overview.view["directions"] and self.limits.tool_attempts > 1)
        ):
            raise AgentError("failure_investigation_required")
        cards = []
        inspected = {
            fact.view["direction"]["direction"]
            for fact in self.facts.values()
            if fact.tool == INSPECT
        }
        for identifier in args.selected_fact_ids:
            fact = self.facts.get(identifier)
            if fact is None or fact.tool != INSPECT:
                raise AgentError("unverified_failure_finding")
            summary = fact.view["direction"]
            direction = self.directions[summary["direction"]]
            cards.append(
                {
                    **fact.view,
                    "problem": direction.problem,
                    "test_point": direction.point,
                    "snapshot_id": self.source.fingerprint,
                    "next_check": _CHECKS[direction.status],
                    "coverage_is_bounded": True,
                    "other_directions_not_inspected": len(self.directions.keys() - inspected),
                    "overview_limited": overview.view["limited"],
                    "source_queries": [
                        {"fact_id": identifier, "metric_key": source.key}
                        for source in fact.item_sources
                    ],
                    "public_display_allowed": False,
                }
            )
        return tuple(cards)

    def resolve(self, fact_id, metric_key):
        self.source.provider.require_details(self.source.scope)
        witnesses = super().resolve(fact_id, metric_key)
        self.source.provider.require_details(self.source.scope)
        return witnesses


class ProgrammingPlanner(QwenAdaptivePlanner):
    """Same native tool dialogue, network guard and campaign accounting as before."""

    decision_type = ProgrammingDecision

    def _admit_execution(self, budget, transport, allow_network):
        # A future Live adapter must bind a trusted programming DB source and its
        # task to reviewed admission and a server-owned budget before using this loop.
        if allow_network or budget.origin != "http_mock":
            raise AgentError("programming_live_not_available")
        if type(budget.authorization) is not OfflineProgrammingPermit:
            raise AgentError("offline_programming_binding_required")
        super()._admit_execution(budget, transport, allow_network)

    def _tool_schemas(self, executor):
        return SCHEMAS

    def __init__(self, executor, budget, **kwargs):
        super().__init__(executor, budget, **kwargs)
        self.history[0]["content"] = (
            "Investigate programming failure patterns within the server-selected problem scope. "
            "First call get_programming_overview with no arguments. From the returned directions "
            "choose inspect_failure_evidence with its direction, overview_id and basis_fact_ids "
            "containing that overview ID. Coverage is the verified denominator. "
            "Investigate different phenomena separately. Finish using only inspected fact IDs, "
            "or with no findings when no direction is available. Never invent causes or use "
            "raw code, names, paths or external references."
        )

    def check_decision(self, decision):
        super().check_decision(decision)
        if decision.name == INSPECT and decision.basis_fact_ids != [
            decision.arguments["overview_id"]
        ]:
            raise AgentError("overview_basis_required")

    async def _post(self, payload):
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()) > (
            self._executor.limits.prompt_bytes
        ):
            raise AgentError("prompt_size_limit")
        return await super()._post(payload)


async def investigate_programming(
    source: ProgrammingSource,
    question: str | tuple[str, ...],
    budget: CampaignBudget,
    *,
    transport: httpx.MockTransport,
    limits: Limits | None = None,
) -> tuple[AgentRun, ProgrammingExecutor]:
    """Offline entry for a bounded command or server-resolved problem IDs."""
    if (
        budget.origin != "http_mock"
        or type(budget.authorization) is not OfflineProgrammingPermit
        or budget.authorization.source is not source
        or type(transport) is not httpx.MockTransport
    ):
        raise AgentError("offline_programming_binding_required")
    return await _run_programming(
        source,
        question,
        budget,
        limits=limits,
        planner_factory=partial(ProgrammingPlanner, transport=transport),
    )


async def _run_programming(
    source: ProgrammingSource,
    question: str | tuple[str, ...],
    budget: CampaignBudget,
    *,
    limits: Limits | None,
    planner_factory: Callable[[ProgrammingExecutor, CampaignBudget], ProgrammingPlanner],
) -> tuple[AgentRun, ProgrammingExecutor]:
    """Run a trusted, server-selected planner on the caller's existing budget."""
    problems = scope_for_question(question, source) if isinstance(question, str) else question
    limits = limits or Limits()
    if (
        type(problems) is not tuple
        or not 1 <= len(problems) <= 20
        or limits.tool_attempts < 2
        or limits.model_requests < 3
    ):
        raise AgentError("invalid_programming_investigation_scope_or_budget")
    executor = ProgrammingExecutor(source, "programming-" + uuid4().hex, limits, problems)
    executor.durable_audit = getattr(budget, "audit_tool", None)
    planner = planner_factory(executor, budget)
    try:
        run = await _run_session(
            executor,
            planner,
            limits=limits,
            decision_type=ProgrammingDecision,
            argument_parser=executor.parse,
            analysis_context=executor.kernel.catalog(limits.minimum_cell),
        )
        executor.verify_all()
        return run, executor
    finally:
        await planner.aclose()
