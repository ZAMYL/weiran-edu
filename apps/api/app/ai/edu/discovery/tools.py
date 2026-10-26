"""Typed targeted computation, shared qualification, source validation and disclosure."""

import json
from decimal import Decimal
from fractions import Fraction
from time import perf_counter

from pydantic import ValidationError

from app.analysis.edu.discovery import BoundaryRequest, TargetedDiscovery, present, sources
from app.analysis.edu.discovery.models import (
    VERSION,
    BoundaryComparison,
    GroupComparison,
    RareCombination,
    SourceSet,
)
from app.analysis.edu.discovery.presentation import exact
from app.analysis.edu.engine import summarize
from app.analysis.edu.models import AnalysisInputError, Condition, Dimension, Operator

from .disclosure import check_disclosure
from .models import SCHEMAS, AgentError, Fact, Limits
from .projection import model_discovery, model_investigation, model_ranking


def conditions(items) -> tuple[Condition, ...]:
    return tuple(
        Condition(Dimension[p.dimension], Operator(p.operator), Fraction(Decimal(p.threshold)))
        for p in items
    )


def parse_arguments(name: str, arguments: dict):
    try:
        model = SCHEMAS[name].model_validate(arguments)
        if (
            name in {"query_dimension_relation", "inspect_rare_combination"}
            and model.left == model.right
        ):
            raise ValueError
        if hasattr(model, "conditions"):
            cs = conditions(model.conditions)
            if len({c.dimension for c in cs}) != len(cs) or any(c.threshold > 5 for c in cs):
                raise ValueError
        if name == "compare_student_groups" and (
            len(set(model.outcomes)) != len(model.outcomes)
            or any(d in {p.dimension for p in model.conditions} for d in model.outcomes)
        ):
            raise ValueError
        return model
    except (KeyError, ValueError, ValidationError):
        raise AgentError("invalid_tool_arguments") from None


def canonical(name: str, model) -> str:
    value = model.model_dump()
    if hasattr(model, "conditions"):
        value["conditions"] = sorted(
            (p.dimension, p.operator, str(Fraction(Decimal(p.threshold)))) for p in model.conditions
        )
    if "outcomes" in value:
        value["outcomes"] = sorted(value["outcomes"])
    if "left" in value:
        value["left"], value["right"] = sorted((value["left"], value["right"]))
    return name + json.dumps(value, sort_keys=True, separators=(",", ":"))


class ToolExecutor:
    def __init__(self, builder: TargetedDiscovery, run_id: str, limits: Limits):
        self.builder, self.run_id, self.limits = builder, run_id, limits
        self.facts: dict[str, Fact] = {}
        self.released: list[frozenset] = [frozenset(builder.scope.records)]
        self.seen: set[str] = set()
        self.discoveries: set[object] = set()
        self.output_bytes = 0
        self.compute_seconds = 0.0

    def _gate(self, sets: list[tuple], *, staged: list[frozenset]) -> list[frozenset]:
        return check_disclosure(
            sets, released=self.released, staged=staged, minimum_cell=self.limits.minimum_cell
        )

    def _validate_discovery(self, discovery) -> None:
        try:
            present(discovery)  # Exact-value / denominator invariant checks.
        except AnalysisInputError:
            raise AgentError("source_validation_failed") from None
        allowed = set(self.builder.scope.records)
        if discovery.context != self.builder.context or discovery.rule_version != VERSION:
            raise AgentError("source_scope_mismatch")
        for source in discovery.sources:
            try:
                rows = sources(discovery, source.metric_key)
            except AnalysisInputError:
                raise AgentError("source_validation_failed") from None
            if any(
                ref not in allowed or ref.batch_id != self.builder.context.batch_id
                for ref, _ in rows
            ):
                raise AgentError("source_scope_mismatch")

    def _card(self, discovery) -> dict:
        """Local teacher display only; this object is never sent to the planner."""
        self._validate_discovery(discovery)
        card = present(discovery)
        card.pop("controlled_queries")
        payload = discovery.payload
        if isinstance(payload, GroupComparison):
            outcome = next(
                d for d in payload.dimensions if d.dimension is payload.outcome_dimension
            )
            card["chart"] = [
                {
                    "dimension": outcome.dimension.name,
                    "target_mean": exact(outcome.target.mean),
                    "remainder_mean": exact(outcome.remainder.mean),
                    "difference": exact(outcome.difference),
                    "target_valid": outcome.target.valid_count,
                    "remainder_valid": outcome.remainder.valid_count,
                }
            ]
        elif isinstance(payload, RareCombination):
            card["chart"] = [
                {"label": "review_group", "count": len(payload.matches)},
                {"label": "remaining_pairs", "count": payload.pair_count - len(payload.matches)},
            ]
        elif not isinstance(payload, BoundaryComparison):
            card["chart"] = [
                {"rho": card["metrics"]["rho"], "paired_count": card["metrics"]["paired_count"]}
            ]
        return card

    @staticmethod
    def _identity(discovery):
        payload = discovery.payload
        if isinstance(payload, GroupComparison):
            return (
                discovery.type,
                frozenset((frozenset(payload.selection.matches), frozenset(payload.remainder))),
                frozenset(c.dimension for c in payload.selection.conditions),
                payload.outcome_dimension,
            )
        if isinstance(payload, BoundaryComparison):
            return (
                discovery.type,
                frozenset(payload.strict.matches),
                frozenset(payload.inclusive.matches),
                frozenset((c.dimension, c.threshold) for c in payload.strict.conditions),
            )
        return discovery.discovery_id

    def execute(self, name: str, model) -> tuple[Fact, ...]:
        started = perf_counter()
        try:
            return self._execute(name, model)
        finally:
            self.compute_seconds += perf_counter() - started

    def _execute(self, name: str, model) -> tuple[Fact, ...]:
        key = canonical(name, model)
        if key in self.seen:
            raise AgentError("repeated_tool")
        self.seen.add(key)
        pending = []
        metric_sources = []
        candidates = []
        b = self.builder
        try:
            if name == "get_dimension_statistics":
                summary = summarize(
                    b.dataset, batch_id=b.context.batch_id, student_ids=b.scope.student_ids
                )
                dims = []
                for s in summary.dimensions:
                    # Suppress small/missing subcells rather than leak individual observations.
                    if s.valid_count < self.limits.minimum_cell or any(
                        0 < n < self.limits.minimum_cell for n in (s.missing_count, s.invalid_count)
                    ):
                        dims.append(
                            {"dimension": s.dimension.name, "state": "privacy_or_data_limited"}
                        )
                        continue
                    try:
                        permitted = self._gate([s.valid_records], staged=pending)
                    except AgentError:
                        dims.append(
                            {"dimension": s.dimension.name, "state": "privacy_or_data_limited"}
                        )
                        continue
                    pending.extend(permitted)
                    metric_sources.append(
                        SourceSet("stats." + s.dimension.name, (s.dimension,), s.valid_records)
                    )
                    dims.append(
                        {
                            "dimension": s.dimension.name,
                            "state": "available",
                            "valid_count": s.valid_count,
                            "missing_count": s.missing_count,
                            "invalid_count": s.invalid_count,
                            "mean": exact(s.mean),
                            "median": exact(s.median),
                            "has_variance": s.minimum != s.maximum,
                            "exploration_thresholds": [
                                str(f.score.numerator)
                                if f.score.denominator == 1
                                else format(
                                    Decimal(f.score.numerator) / Decimal(f.score.denominator), "f"
                                )
                                for f in s.distribution
                                if f.share.count >= self.limits.minimum_cell
                                and (
                                    f.share.count == s.valid_count
                                    or s.valid_count - f.share.count >= self.limits.minimum_cell
                                )
                            ][:8],
                        }
                    )
                # Empty/small scopes disclose only a fixed insufficient state, no small counts.
                view = (
                    {
                        "state": "available",
                        "student_denominator": summary.scope.student_count,
                        "dimensions": dims,
                    }
                    if summary.scope.student_count >= self.limits.minimum_cell
                    else {"state": "insufficient_data", "dimensions": []}
                )
                candidates = [(view, None)]
            elif name == "query_dimension_relation":
                pair, discovery = b.relation(Dimension[model.left], Dimension[model.right])
                metric_sources = [
                    SourceSet(
                        "paired_scores",
                        (pair.left, pair.right),
                        tuple(o.reference for o in pair.observations),
                    )
                ]
                if pair.paired_count < self.limits.minimum_cell:
                    candidates = [({"state": "insufficient_data"}, None)]
                else:
                    pending = self._gate(
                        [tuple(o.reference for o in pair.observations)], staged=pending
                    )
                    candidates = [
                        (
                            {
                                "state": pair.state.value,
                                "left": pair.left.name,
                                "right": pair.right.name,
                                "rho": pair.coefficient,
                                "paired_count": pair.paired_count,
                                "excluded_count": len(pair.excluded_records),
                                "student_denominator": b.scope.student_count,
                            },
                            discovery,
                        )
                    ]
            elif name == "compare_student_groups":
                comparison, qualified = b.group(
                    conditions(model.conditions), tuple(Dimension[d] for d in model.outcomes)
                )
                pending = self._gate(
                    [
                        comparison.selection.matches,
                        comparison.remainder,
                        comparison.selection.unevaluable_records,
                    ],
                    staged=pending,
                )
                metric_sources = [
                    SourceSet(
                        "target_members",
                        tuple(c.dimension for c in comparison.selection.conditions),
                        comparison.selection.matches,
                    ),
                    SourceSet(
                        "remainder_members",
                        tuple(c.dimension for c in comparison.selection.conditions),
                        comparison.remainder,
                    ),
                ]
                differences = []
                for d in comparison.dimensions:
                    if d.dimension.name in model.outcomes:
                        pending.extend(
                            self._gate(
                                [d.target.valid_records, d.remainder.valid_records], staged=pending
                            )
                        )
                        metric_sources.extend(
                            (
                                SourceSet(
                                    "target." + d.dimension.name,
                                    (d.dimension,),
                                    d.target.valid_records,
                                ),
                                SourceSet(
                                    "remainder." + d.dimension.name,
                                    (d.dimension,),
                                    d.remainder.valid_records,
                                ),
                            )
                        )
                        differences.append(
                            {
                                "dimension": d.dimension.name,
                                "target_valid": d.target.valid_count,
                                "remainder_valid": d.remainder.valid_count,
                                "target_mean": exact(d.target.mean),
                                "remainder_mean": exact(d.remainder.mean),
                                "difference": exact(d.difference),
                            }
                        )
                view = {
                    "target_count": len(comparison.selection.matches),
                    "remainder_count": len(comparison.remainder),
                    "unevaluable_count": len(comparison.selection.unevaluable_records),
                    "student_denominator": b.scope.student_count,
                    "eligible_denominator": comparison.selection.eligible_count,
                    "differences": differences,
                }
                candidates = [(view, d) for d in qualified] or [(view, None)]
            elif name == "inspect_rare_combination":
                pair, qualified = b.rare(Dimension[model.left], Dimension[model.right])
                metric_sources = [
                    SourceSet(
                        "paired_scores",
                        (pair.left, pair.right),
                        tuple(o.reference for o in pair.observations),
                    )
                ]
                if pair.paired_count < self.limits.minimum_cell:
                    candidates = [({"state": "insufficient_data"}, None)]
                elif qualified:
                    for d in qualified:
                        pending.extend(
                            self._gate([d.payload.matches, d.payload.pair_records], staged=pending)
                        )
                    candidates = [({"state": "computed"}, d) for d in qualified]
                else:
                    pending = self._gate(
                        [tuple(o.reference for o in pair.observations)], staged=pending
                    )
                    candidates = [
                        (
                            {"state": "no_qualified_discovery", "paired_count": pair.paired_count},
                            None,
                        )
                    ]
            elif name == "verify_threshold_sensitivity":
                result, discovery = b.boundary(
                    BoundaryRequest(
                        conditions(model.conditions), Dimension[model.varying_dimension]
                    )
                )
                metric_sources = [
                    SourceSet(
                        "strict_members",
                        tuple(c.dimension for c in result.strict.conditions),
                        result.strict.matches,
                    ),
                    SourceSet(
                        "inclusive_members",
                        tuple(c.dimension for c in result.inclusive.conditions),
                        result.inclusive.matches,
                    ),
                    SourceSet(
                        "added_members",
                        tuple(c.dimension for c in result.inclusive.conditions),
                        result.added,
                    ),
                ]
                pending = self._gate(
                    [
                        result.strict.matches,
                        result.inclusive.matches,
                        result.added,
                        result.strict.unevaluable_records,
                    ],
                    staged=pending,
                )
                candidates = [
                    (
                        {
                            "strict_count": len(result.strict.matches),
                            "inclusive_count": len(result.inclusive.matches),
                            "added_count": len(result.added),
                            "unevaluable_count": len(result.strict.unevaluable_records),
                            "student_denominator": b.scope.student_count,
                            "eligible_denominator": result.strict.eligible_count,
                            "observed_boundary": result.observed_boundary,
                        },
                        discovery,
                    )
                ]
            elif name == "inspect_discovery_support":
                fact = self.facts.get(model.fact_id)
                if fact is None:
                    raise AgentError("unknown_fact_id")
                metric_sources = list(fact.metric_sources)
                if fact.discovery is None:
                    candidates = [
                        (
                            {
                                "checked_fact_id": fact.fact_id,
                                "qualification": "investigation_only",
                            },
                            None,
                        )
                    ]
                else:
                    self._validate_discovery(fact.discovery)
                    candidates = [
                        (
                            {
                                "checked_fact_id": fact.fact_id,
                                "qualification": "qualified",
                                "ranking": model_ranking(fact.discovery.ranking),
                                "status": fact.discovery.status,
                            },
                            None,
                        )
                    ]
            else:
                raise AgentError("unknown_tool")
        except AnalysisInputError:
            raise AgentError("analysis_rejected") from None
        output = []
        for view, discovery in candidates:
            view = model_investigation(name, view)
            if discovery is not None:
                self._validate_discovery(discovery)
                card = model_discovery(discovery)
                if self._identity(discovery) in self.discoveries:
                    raise AgentError("duplicate_discovery")
                view = {**view, "card": card}
            fact_id = f"{self.run_id}-f{len(self.facts) + len(output) + 1:03}"
            selected_sources = (
                tuple(discovery.sources) if discovery is not None else tuple(metric_sources)
            )
            if any(not set(s.records) <= set(b.scope.records) for s in selected_sources):
                raise AgentError("source_scope_mismatch")
            output.append(
                Fact(
                    fact_id,
                    name,
                    {"fact_id": fact_id, "qualified": discovery is not None, **view},
                    discovery,
                    selected_sources,
                )
            )
        raw = json.dumps([f.view for f in output], ensure_ascii=False, allow_nan=False).encode()
        if (
            len(raw) > self.limits.output_bytes
            or self.output_bytes + len(raw) > self.limits.total_output_bytes
        ):
            raise AgentError("tool_output_limit")
        # Publish atomically only after source checks, disclosure and output budgets pass.
        self.output_bytes += len(raw)
        self.released.extend(pending)
        for f in output:
            self.facts[f.fact_id] = f
            if f.discovery is not None:
                self.discoveries.add(self._identity(f.discovery))
        return tuple(output)

    def finish(self, model) -> tuple[dict, ...]:
        if len(set(model.selected_fact_ids)) != len(model.selected_fact_ids):
            raise AgentError("duplicate_selection")
        selected = []
        for identity in model.selected_fact_ids:
            fact = self.facts.get(identity)
            if fact is None:
                raise AgentError("unknown_fact_id")
            if fact.discovery is None:
                raise AgentError("unqualified_fact")
            selected.append(self._card(fact.discovery))
        if bool(selected) != (model.reason == "completed"):
            raise AgentError("inconsistent_stop_reason")
        return tuple(selected)
