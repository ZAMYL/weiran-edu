"""Adaptive typed executor using #49 session, budget, trace and disclosure policy."""

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from time import perf_counter
from uuid import uuid4

from app.ai.edu.discovery.disclosure import check_disclosure
from app.ai.edu.discovery.models import (
    AgentError,
    Fact,
    FactId,
    FinishArgs,
    Limits,
    Strict,
    SupportArgs,
)
from app.ai.edu.discovery.session import run_session
from app.analysis.edu.discovery.presentation import exact
from pydantic import Field, ValidationError

from docs.canonical_assessment.model import exact as decimal_value

from .analysis import VERSION, Calculation, Kernel, MetricSource
from .data import PrototypeError
from .synthetic import AdmittedSynthetic, admit


class ItemArgs(Strict):
    item_id: str = Field(pattern=r"^[IQ][0-9]{2,3}$")


class PairArgs(Strict):
    left: str = Field(pattern=r"^[IQ][0-9]{2,3}$")
    right: str = Field(pattern=r"^[IQ][0-9]{2,3}$")


class Predicate(Strict):
    item_id: str = Field(pattern=r"^[IQ][0-9]{2,3}$")
    operator: str = Field(pattern=r"^(<|<=|==|>=|>)$")
    threshold: str = Field(pattern=r"^[+-]?[0-9]{1,5}(?:\.[0-9]{1,2})?$", max_length=10)


class GroupArgs(Strict):
    conditions: list[Predicate] = Field(min_length=1, max_length=2)
    outcomes: list[str] = Field(min_length=1, max_length=1)


class StatisticsArgs(Strict):
    item_ids: list[str] | None = Field(default=None, min_length=1, max_length=3)


SCHEMAS = {
    "get_dimension_statistics": StatisticsArgs,
    "query_dimension_relation": PairArgs,
    "compare_student_groups": GroupArgs,
    "inspect_item_quality": ItemArgs,
    "inspect_discovery_support": SupportArgs,
    "finish": FinishArgs,
}


class Decision(Strict):
    name: str
    arguments: dict[str, object]
    basis_fact_ids: list[FactId] = Field(default_factory=list, max_length=8)

    def model_post_init(self, context):
        if self.name not in SCHEMAS:
            raise ValueError("unknown_tool")


def wire(value):
    if isinstance(value, Fraction):
        return exact(value)
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire(v) for v in value]
    return value


@dataclass(frozen=True)
class AdaptiveFact(Fact):
    query: dict = field(default_factory=dict, repr=False)
    calculation: Calculation | None = field(default=None, repr=False)
    item_sources: tuple[MetricSource, ...] = field(default=(), repr=False)
    snapshot_id: str = ""
    view_sha256: str = ""
    proof_sha256: str = ""


class AdaptiveExecutor:
    def __init__(self, source: AdmittedSynthetic, run_id: str, limits: Limits):
        source.validate()
        self.source, self.run_id, self.limits = source, run_id, limits
        self.kernel = Kernel(source.envelope)
        self.facts = {}
        self.released = [frozenset(self.kernel.participants)]
        self.seen = set()
        self.identities = set()
        self.output_bytes = 0
        self.compute_seconds = 0.0

    def parse(self, name, arguments):
        try:
            result = SCHEMAS[name].model_validate(arguments)
            if name == "get_dimension_statistics" and result.item_ids is not None:
                if len(set(result.item_ids)) != len(result.item_ids):
                    raise ValueError
                for item_id in result.item_ids:
                    self.kernel.item(item_id)
            if name in ("query_dimension_relation",):
                self.kernel.item(result.left)
                self.kernel.item(result.right)
                if result.left == result.right:
                    raise ValueError
            elif name == "inspect_item_quality":
                self.kernel.item(result.item_id)
            elif name == "compare_student_groups":
                for p in result.conditions:
                    self.kernel.item(p.item_id)
                for item in result.outcomes:
                    self.kernel.item(item)
                if len({p.item_id for p in result.conditions}) != len(result.conditions):
                    raise ValueError
            return result
        except (ValueError, KeyError, ValidationError):
            raise AgentError("invalid_tool_arguments") from None

    def _gate(self, sources, staged):
        return check_disclosure(
            [s.participants for s in sources],
            released=self.released,
            staged=staged,
            minimum_cell=self.limits.minimum_cell,
        )

    def calculate(self, name, args):
        return self.derive(self.kernel, name, args)

    @staticmethod
    def derive(kernel, name, args):
        if name == "query_dimension_relation":
            return kernel.relation(args.left, args.right)
        if name == "compare_student_groups":
            return kernel.group(args.conditions, args.outcomes[0])
        if name == "inspect_item_quality":
            return kernel.quality(args.item_id)
        raise AgentError("unknown_calculation")

    def _summary(self, staged, item_ids):
        items = []
        sources = []
        k = self.limits.minimum_cell
        for item_id in self.kernel.items:
            if item_ids is not None and item_id not in item_ids:
                continue
            stats, valid_source = self.kernel.statistics(item_id)
            if stats["denominator"] < k or any(0 < n < k for n in stats["states"].values()):
                items.append({"item_id": item_id, "state": "privacy_or_data_limited"})
                continue
            categories = []
            for state in stats["states"]:
                ids = tuple(pid for pid, _, s in self.kernel.columns[item_id].values if s == state)
                categories.append(
                    self.kernel.source(item_id + ".state." + state, item_id, ids, "row")
                )
            try:
                checked = self._gate([valid_source, *categories], staged)
            except AgentError:
                items.append({"item_id": item_id, "state": "privacy_or_data_limited"})
                continue
            staged.extend(checked)
            sources.extend((valid_source, *categories))
            projected = {
                key: stats[key]
                for key in (
                    "item_id",
                    "denominator",
                    "valid_count",
                    "excluded_count",
                    "states",
                    "mean",
                    "median",
                    "range_checked",
                )
            }
            projected["state"] = (
                "available" if stats["valid_count"] >= k else "no_authorized_numeric_data"
            )
            projected["has_variance"] = len(stats["distribution"]) > 1
            projected["exploration_thresholds"] = [
                format(Decimal(value.numerator) / Decimal(value.denominator), "f")
                for value, count in stats["distribution"]
                if count >= k
                and (count == stats["valid_count"] or stats["valid_count"] - count >= k)
            ][:8]
            bins, bin_sources = self.kernel.coarse(item_id) if item_ids is not None else ((), ())
            if bins:
                try:
                    checked = self._gate(bin_sources, staged)
                except AgentError:
                    projected["distribution_state"] = "privacy_limited"
                else:
                    staged.extend(checked)
                    sources.extend(bin_sources)
                    projected["coarse_distribution"] = bins
            items.append(projected)
        return {
            "items": items,
            "state": "available" if len(self.kernel.participants) >= k else "insufficient_data",
        }, tuple(sources)

    @staticmethod
    def _identity(calculation):
        if calculation.kind == "item_group_difference":
            a = next(
                s.participants for s in calculation.sources if s.key.startswith("target.condition.")
            )
            b = next(
                s.participants
                for s in calculation.sources
                if s.key.startswith("remainder.condition.")
            )
            return (
                calculation.kind,
                frozenset((frozenset(a), frozenset(b))),
                frozenset(p["item_id"] for p in calculation.values["conditions"]),
                calculation.values["outcome"],
            )
        if calculation.kind == "item_relation":
            return calculation.kind, frozenset(
                (calculation.values["left"], calculation.values["right"])
            )
        return calculation.kind, calculation.values["item_id"]

    def execute(self, name, args):
        started = perf_counter()
        try:
            return self._execute(name, args)
        except PrototypeError as exc:
            raise AgentError(str(exc)) from None
        finally:
            self.compute_seconds += perf_counter() - started

    def _execute(self, name, args):
        query = {"tool": name, "arguments": args.model_dump()}
        key = json.dumps(query, sort_keys=True)
        if name == "query_dimension_relation":
            key = name + str(sorted((args.left, args.right)))
        elif name == "compare_student_groups":
            key = (
                name
                + str(
                    sorted(
                        (p.item_id, p.operator, decimal_value(p.threshold)) for p in args.conditions
                    )
                )
                + str(args.outcomes)
            )
        if key in self.seen:
            raise AgentError("repeated_tool")
        self.seen.add(key)
        self.source.validate()
        staged = []
        calculation = None
        identity = None
        if name == "get_dimension_statistics":
            projection, sources = self._summary(staged, args.item_ids)
        elif name == "inspect_discovery_support":
            original = self.facts.get(args.fact_id)
            if original is None:
                raise AgentError("unknown_fact_id")
            self._verify(original)
            sources = original.item_sources
            projection = {
                "checked_fact_id": original.fact_id,
                "qualification": "qualified"
                if original.view["qualified"]
                else "investigation_only",
            }
            if original.calculation:
                projection["ranking"] = original.calculation.ranking
                projection["limitations"] = original.calculation.limitations
        else:
            calculation = self.calculate(name, args)
            if calculation != self.derive(Kernel(self.source.envelope), name, args):
                raise AgentError("source_validation_failed")
            sources = calculation.sources
            staged.extend(self._gate(sources, staged))
            projection = {
                "metrics": calculation.values,
                "status": calculation.status,
                "ranking": calculation.ranking,
                "limitation_codes": calculation.limitations,
                "type": calculation.kind,
            }
            if calculation.qualified:
                identity = self._identity(calculation)
                if identity in self.identities:
                    raise AgentError("duplicate_discovery")
        identity_id = f"{self.run_id}-f{len(self.facts) + 1:03}"
        view = wire(
            {
                "fact_id": identity_id,
                "qualified": calculation.qualified if calculation else False,
                **projection,
            }
        )
        raw = json.dumps(view, ensure_ascii=False, allow_nan=False).encode()
        if (
            len(raw) > self.limits.output_bytes
            or self.output_bytes + len(raw) > self.limits.total_output_bytes
        ):
            raise AgentError("tool_output_limit")
        for source in sources:
            if (
                len(source.participants) != len(source.references)
                or len(set(source.participants)) != len(source.participants)
                or not set(source.participants) <= set(self.kernel.participants)
            ):
                raise AgentError("source_scope_mismatch")
            for pid, ref in zip(source.participants, source.references, strict=True):
                witness = self.kernel.witnesses.get(ref)
                if (
                    witness is None
                    or witness.participant_id != pid
                    or witness.item_id != source.item_id
                    or witness.source_sha256 != self.source.fingerprint
                ):
                    raise AgentError("source_scope_mismatch")
        fact = AdaptiveFact(
            identity_id,
            name,
            view,
            query=query,
            calculation=calculation,
            item_sources=sources,
            snapshot_id=self.source.fingerprint,
            proof_sha256=sha256(
                json.dumps([asdict(s) for s in sources], sort_keys=True).encode()
            ).hexdigest(),
            view_sha256=sha256(
                json.dumps(view, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
            ).hexdigest(),
        )
        self.output_bytes += len(raw)
        self.released.extend(staged)
        self.facts[identity_id] = fact
        if identity is not None:
            self.identities.add(identity)
        return (fact,)

    def _verify(self, fact):
        try:
            self.source.validate()
        except PrototypeError:
            raise AgentError("source_validation_failed") from None
        if (
            sha256(
                json.dumps(fact.view, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
            ).hexdigest()
            != fact.view_sha256
        ):
            raise AgentError("source_validation_failed")
        if (
            sha256(
                json.dumps([asdict(s) for s in fact.item_sources], sort_keys=True).encode()
            ).hexdigest()
            != fact.proof_sha256
        ):
            raise AgentError("source_validation_failed")
        if fact.snapshot_id != self.source.fingerprint:
            raise AgentError("source_scope_mismatch")
        if fact.calculation is not None:
            args = self.parse(fact.query["tool"], fact.query["arguments"])
            actual = self.derive(Kernel(self.source.envelope), fact.query["tool"], args)
            if actual != fact.calculation or actual.sources != fact.item_sources:
                raise AgentError("source_validation_failed")
            expected = wire(
                {
                    "fact_id": fact.fact_id,
                    "qualified": actual.qualified,
                    "metrics": actual.values,
                    "status": actual.status,
                    "ranking": actual.ranking,
                    "limitation_codes": actual.limitations,
                    "type": actual.kind,
                }
            )
            if fact.view != expected:
                raise AgentError("source_validation_failed")

    def finish(self, args):
        if len(set(args.selected_fact_ids)) != len(args.selected_fact_ids):
            raise AgentError("duplicate_selection")
        cards = []
        for identifier in args.selected_fact_ids:
            fact = self.facts.get(identifier)
            if fact is None:
                raise AgentError("unknown_fact_id")
            self._verify(fact)
            calculation = fact.calculation
            if calculation is None or not calculation.qualified:
                raise AgentError("unqualified_fact")
            values = calculation.values
            if calculation.kind == "item_group_difference":
                title = f"{values['outcome']} 的互斥群体描述差异"
                summary = (
                    f"目标{values['target_count']}人，余组{values['remainder_count']}人，"
                    f"均值差{values['difference']}；不表示因果或能力等级。"
                )
            elif calculation.kind == "item_relation":
                title = f"{values['left']} 与 {values['right']} 的评分关联"
                summary = (
                    f"有效成对{values['paired_count']}人，Spearman={values['rho']:.6g}；"
                    "相关不代表因果。"
                )
            else:
                title = f"{values['item_id']} 的资料复核现象"
                summary = (
                    f"总人数{values['denominator']}，状态计数{values['counts']}；"
                    "缺失不当零分，不判断成绩对错。"
                )
            cards.append(
                wire(
                    {
                        "fact_id": identifier,
                        "discovery_id": sha256(
                            (
                                self.source.fingerprint
                                + json.dumps(wire(calculation.values), sort_keys=True)
                            ).encode()
                        ).hexdigest()[:24],
                        "type": calculation.kind,
                        "title": title,
                        "summary": summary,
                        "batch_id": self.kernel.data.batch.batch_id,
                        "snapshot_id": self.source.fingerprint,
                        "rule_version": VERSION,
                        "metrics": values,
                        "chart": values,
                        "status": calculation.status,
                        "limitations": calculation.limitations,
                        "ranking": calculation.ranking,
                        "public_display_allowed": False,
                        "source_queries": [
                            {"fact_id": identifier, "metric_key": s.key}
                            for s in calculation.sources
                        ],
                    }
                )
            )
        if bool(cards) != (args.reason == "completed"):
            raise AgentError("inconsistent_stop_reason")
        return tuple(cards)

    def resolve(self, fact_id, metric_key):
        fact = self.facts.get(fact_id)
        if fact is None:
            raise AgentError("unknown_fact_id")
        self._verify(fact)
        source = next((s for s in fact.item_sources if s.key == metric_key), None)
        if source is None:
            raise AgentError("unknown_metric_key")
        return tuple(self.kernel.witnesses[ref] for ref in source.references)


async def run_adaptive(recipe: str, planner, *, limits: Limits | None = None, mode="offline"):
    if mode != "offline" or getattr(planner, "offline_only", False) is not True:
        raise AgentError("remote_discovery_disabled")
    limits = limits or Limits()
    source = admit(recipe)
    executor = AdaptiveExecutor(source, "run-" + uuid4().hex[:16], limits)
    run = await run_session(
        executor,
        planner,
        limits=limits,
        decision_type=Decision,
        argument_parser=executor.parse,
        analysis_context=executor.kernel.catalog(limits.minimum_cell),
    )
    return run, executor
