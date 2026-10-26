"""Dynamic item calculations: stdlib exact statistics + existing ranks, policy and ranking.

No conversion to five abilities, cross-item score sums, causal or growth diagnosis.
"""

from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from statistics import correlation, mean, median

from app.analysis.edu.correlation import average_ranks
from app.analysis.edu.discovery.engine import _ranking, _status
from app.analysis.edu.discovery.models import Policy, Robustness
from app.analysis.edu.engine import COMPARATORS
from app.analysis.edu.models import Operator

from docs.canonical_assessment.model import exact
from docs.canonical_assessment.readiness import assess, score_state

from .data import Envelope, PrototypeError

VERSION = "edu-adaptive-prototype-0.1-draft"


@dataclass(frozen=True)
class MetricSource:
    key: str
    item_id: str
    participants: tuple[str, ...] = field(repr=False)
    references: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class Column:
    item_id: str
    values: tuple[tuple[str, Fraction | None, str], ...] = field(repr=False)


@dataclass(frozen=True)
class Calculation:
    kind: str
    values: dict
    sources: tuple[MetricSource, ...] = field(repr=False)
    qualified: bool
    status: str
    ranking: dict
    limitations: tuple[str, ...]


class Kernel:
    def __init__(self, envelope: Envelope):
        envelope.validate()
        self.envelope = envelope
        self.data = envelope.data
        self.readiness = assess(self.data, "teacher_analysis")
        self.items = {item.item_id: item for item in self.data.items}
        self.levels = dict(envelope.material.levels)
        self.participants = tuple(p.participant_id for p in self.data.participants)
        self.observations = {(o.participant_id, o.item_id): o for o in self.data.observations}
        self.evidence = {e.evidence_id: e for e in self.data.evidence}
        self.witnesses = {w.reference_id: w for w in envelope.material.witnesses}
        self.policy = Policy()
        self.columns = {item: self._column(item) for item in self.items}

    def _column(self, item_id: str) -> Column:
        values = []
        for pid in self.participants:
            observation = self.observations.get((pid, item_id))
            refs = (
                tuple(
                    self.evidence[e]
                    for e in observation.evidence_ids
                    if self.evidence[e].kind == "score"
                )
                if observation
                else ()
            )
            if observation and not refs:
                state = "evidence_absent"
            elif refs and not any(e.provenance in ("retrieved", "user_confirmed") for e in refs):
                state = "evidence_unconfirmed"
            elif refs and not any("teacher_analysis" in e.permitted_purposes for e in refs):
                state = "evidence_withheld"
            else:
                state = score_state(observation, self.items[item_id])
            value = exact(observation.raw_decimal) if state in ("valid", "range_unknown") else None
            values.append((pid, value, state))
        return Column(item_id, tuple(values))

    def item(self, item_id):
        if item_id not in self.items:
            raise PrototypeError("unknown_item")
        return self.items[item_id]

    def eligible(self, item_id, participants=None):
        self.item(item_id)
        allowed = set(self.participants if participants is None else participants)
        return tuple(
            (pid, value)
            for pid, value, _ in self.columns[item_id].values
            if pid in allowed and value is not None
        )

    def source(self, key, item_id, participants, kind="score") -> MetricSource:
        refs = []
        for pid in participants:
            if kind == "row":
                ref = "row-" + pid + "-" + item_id
            else:
                observation = self.observations[(pid, item_id)]
                ref = next(
                    (
                        e
                        for e in observation.evidence_ids
                        if self.evidence[e].kind == "score"
                        and "teacher_analysis" in self.evidence[e].permitted_purposes
                        and self.evidence[e].provenance in ("retrieved", "user_confirmed")
                    ),
                    None,
                )
            if ref not in self.witnesses:
                raise PrototypeError("missing_source_witness")
            witness = self.witnesses[ref]
            if (
                witness.item_id != item_id
                or witness.participant_id != pid
                or witness.source_sha256 != self.envelope.snapshot_id
            ):
                raise PrototypeError("source_binding_mismatch")
            refs.append(ref)
        return MetricSource(key, item_id, tuple(participants), tuple(refs))

    def statistics(self, item_id, participants=None):
        self.item(item_id)
        ids = self.participants if participants is None else tuple(participants)
        valid = self.eligible(item_id, ids)
        values = tuple(v for _, v in valid)
        counts = Counter(values)
        states = Counter(state for pid, _, state in self.columns[item_id].values if pid in set(ids))
        return {
            "item_id": item_id,
            "denominator": len(ids),
            "valid_count": len(values),
            "excluded_count": len(ids) - len(values),
            "states": dict(sorted(states.items())),
            "mean": mean(values) if values else None,
            "median": median(values) if values else None,
            "distribution": tuple((v, counts[v]) for v in sorted(counts)),
            "range_checked": self.items[item_id].scale is not None,
        }, self.source(item_id + ".valid", item_id, tuple(p for p, _ in valid))

    def coarse(self, item_id):
        item = self.item(item_id)
        if item.scale is None:
            return (), ()
        low, high = exact(item.scale.minimum), exact(item.scale.maximum)
        bins = []
        sources = []
        for i in range(4):
            left = low + (high - low) * i / 4
            right = low + (high - low) * (i + 1) / 4
            ids = tuple(
                pid
                for pid, value in self.eligible(item_id)
                if left <= value and (value <= right if i == 3 else value < right)
            )
            bins.append(
                {"lower": left, "upper": right, "upper_inclusive": i == 3, "count": len(ids)}
            )
            sources.append(self.source(f"{item_id}.bin{i}", item_id, ids))
        return tuple(bins), tuple(sources)

    def _pair(self, left, right, participants=None):
        if left == right:
            raise PrototypeError("distinct_items_required")
        for item_id in (left, right):
            item = self.item(item_id)
            if item.scale is None or self.levels[item_id] not in ("ordinal", "interval"):
                raise PrototypeError("relation_scale_or_level_unknown")
        a, b = dict(self.eligible(left, participants)), dict(self.eligible(right, participants))
        ids = tuple(pid for pid in self.participants if pid in a and pid in b)
        x, y = tuple(a[p] for p in ids), tuple(b[p] for p in ids)
        state = (
            "insufficient_pairs"
            if len(ids) < self.policy.minimum_samples
            else "constant_input"
            if len(set(x)) < 2 or len(set(y)) < 2
            else "computable"
        )
        rho = correlation(average_ranks(x), average_ranks(y)) if state == "computable" else None
        return ids, state, rho

    def _folds(self):
        return tuple(
            tuple(pid for i, pid in enumerate(self.participants) if i % 3 != fold)
            for fold in range(3)
        )

    def _rank(self, effect, support, valid, robust):
        rank = _ranking(effect, support, len(self.participants), valid, robust, self.policy)
        return {
            "score": rank.score,
            "support_count": support,
            "robustness": {
                "method": robust.method,
                "planned": robust.planned,
                "evaluated": robust.evaluated,
                "passed": robust.passed,
                "value": robust.value,
            },
            "is_statistical_confidence": False,
        }, _status(rank, self.policy)

    def relation(self, left, right):
        ids, state, rho = self._pair(left, right)
        folded = [self._pair(left, right, ids)[2] for ids in self._folds()]
        evaluated = [v for v in folded if v is not None]
        robust = Robustness(
            "three_fixed_deletions",
            3,
            len(evaluated),
            sum(
                rho is not None and v * rho > 0 and abs(v) >= self.policy.correlation_threshold
                for v in evaluated
            ),
        )
        qualified = rho is not None and abs(rho) >= self.policy.correlation_threshold
        rank, status = self._rank(Fraction(str(abs(rho or 0))), len(ids), len(ids), robust)
        sources = (self.source("left.paired", left, ids), self.source("right.paired", right, ids))
        return Calculation(
            "item_relation",
            {
                "left": left,
                "right": right,
                "rho": rho,
                "paired_count": len(ids),
                "excluded_count": len(self.participants) - len(ids),
                "denominator": len(self.participants),
                "state": state,
            },
            sources,
            qualified,
            status,
            rank,
            ("correlation_not_causation", "ties_use_existing_average_ranks"),
        )

    def group(self, predicates, outcome):
        condition_items = {p.item_id for p in predicates}
        if outcome in condition_items or len(condition_items) != len(predicates):
            raise PrototypeError("distinct_condition_and_outcome_items_required")
        for p in predicates:
            item = self.item(p.item_id)
            if item.scale is None:
                raise PrototypeError("filter_scale_unknown")
            if self.levels[p.item_id] not in ("ordinal", "interval"):
                raise PrototypeError("filter_level_unknown")
            threshold = exact(p.threshold)
            if (
                not exact(item.scale.minimum) <= threshold <= exact(item.scale.maximum)
                or ((threshold - exact(item.scale.minimum)) / exact(item.scale.step)).denominator
                != 1
            ):
                raise PrototypeError("threshold_outside_item_scale")
        item = self.item(outcome)
        if item.scale is None or self.levels[outcome] not in ("ordinal", "interval"):
            raise PrototypeError("outcome_scale_or_level_unknown")
        values = {i: dict(self.eligible(i)) for i in condition_items}
        target = []
        rest = []
        unknown = []
        for pid in self.participants:
            if any(pid not in values[i] for i in condition_items):
                unknown.append(pid)
            elif all(
                COMPARATORS[Operator(p.operator)](values[p.item_id][pid], exact(p.threshold))
                for p in predicates
            ):
                target.append(pid)
            else:
                rest.append(pid)
        a, sa = self.statistics(outcome, target)
        b, sb = self.statistics(outcome, rest)
        delta = a["mean"] - b["mean"] if a["mean"] is not None and b["mean"] is not None else None
        span = exact(item.scale.maximum) - exact(item.scale.minimum)
        # Same scale-relative exploratory effect as #41: 0.5 / (5-1), not a teaching standard.
        effect = abs(delta) / span if delta is not None else Fraction(0)
        qualified = (
            min(a["valid_count"], b["valid_count"]) >= self.policy.minimum_samples
            and effect >= self.policy.mean_difference / 4
        )
        evaluated = passed = 0
        for fold in self._folds():
            ids = set(fold)
            x, _ = self.statistics(outcome, [p for p in target if p in ids])
            y, _ = self.statistics(outcome, [p for p in rest if p in ids])
            if min(x["valid_count"], y["valid_count"]) >= self.policy.minimum_samples:
                evaluated += 1
                diff = x["mean"] - y["mean"]
                passed += (
                    delta is not None
                    and delta * diff > 0
                    and abs(diff) / span >= self.policy.mean_difference / 4
                )
        rank, status = self._rank(
            effect,
            min(a["valid_count"], b["valid_count"]),
            a["valid_count"] + b["valid_count"],
            Robustness("three_fixed_deletions", 3, evaluated, passed),
        )
        sources = [
            self.source("target.valid", outcome, sa.participants),
            self.source("remainder.valid", outcome, sb.participants),
        ]
        for p in predicates:
            sources.extend(
                (
                    self.source("target.condition." + p.item_id, p.item_id, target),
                    self.source("remainder.condition." + p.item_id, p.item_id, rest),
                )
            )
        # Unknown conditions may have no numeric witness; cite actual row declarations instead.
        sources.append(self.source("unevaluable.rows", predicates[0].item_id, unknown, "row"))
        n = len(self.participants)
        eligible = n - len(unknown)
        return Calculation(
            "item_group_difference",
            {
                "outcome": outcome,
                "conditions": [p.model_dump() for p in predicates],
                "target_count": len(target),
                "remainder_count": len(rest),
                "unevaluable_count": len(unknown),
                "denominator": n,
                "eligible_denominator": eligible,
                "share_total": Fraction(len(target), n) if n else None,
                "share_eligible": Fraction(len(target), eligible) if eligible else None,
                "target_valid": a["valid_count"],
                "remainder_valid": b["valid_count"],
                "target_mean": a["mean"],
                "remainder_mean": b["mean"],
                "difference": delta,
                "outcome_unit": item.scale.unit,
            },
            tuple(sources),
            qualified,
            status,
            rank,
            (
                "condition_dimensions_not_discoveries",
                "disjoint_remainder",
                "ordinal_mean_is_descriptive",
                "no_cross_item_score_sum",
            ),
        )

    def quality(self, item_id):
        stats, _ = self.statistics(item_id)
        sources = []
        for state in stats["states"]:
            ids = tuple(pid for pid, _, s in self.columns[item_id].values if s == state)
            sources.append(self.source("state." + state, item_id, ids, "row"))
        rubric = self.items[item_id].rubric_id
        issues = sum(n for state, n in stats["states"].items() if state != "valid")
        return Calculation(
            "item_quality_review",
            {
                "item_id": item_id,
                "denominator": len(self.participants),
                "counts": stats["states"],
                "scale_known": self.items[item_id].scale is not None,
                "rubric_available": rubric is not None,
                "score_changed": False,
            },
            tuple(sources),
            len(self.participants) >= self.policy.minimum_samples
            and bool(issues or rubric is None),
            "interpret_cautiously",
            {},
            ("missing_not_zero", "no_score_correctness_diagnosis", "no_invented_rubric"),
        )

    def catalog(self, minimum_cell=5):
        items = []
        for item_id, item in self.items.items():
            count = len(self.eligible(item_id))
            numeric = count >= minimum_cell
            items.append(
                {
                    "item_id": item_id,
                    "kind": item.kind,
                    "label": item.name,
                    "scale": None
                    if item.scale is None
                    else {
                        "minimum": item.scale.minimum,
                        "maximum": item.scale.maximum,
                        "step": item.scale.step,
                        "unit": item.scale.unit,
                    },
                    "measurement_level": self.levels[item_id],
                    "statistics": (
                        "available"
                        if count == len(self.participants) and item.scale is not None
                        else "partial"
                    )
                    if numeric
                    else "insufficient_or_withheld",
                    "relation_eligible": numeric
                    and item.scale is not None
                    and self.levels[item_id] in ("ordinal", "interval"),
                    "filter_eligible": numeric
                    and item.scale is not None
                    and self.levels[item_id] in ("ordinal", "interval"),
                    "rubric_available": item.rubric_id is not None,
                }
            )
        return {
            "model_view": "adaptive-aggregate-v1",
            "items": items,
            "tools": [
                "get_dimension_statistics",
                "query_dimension_relation",
                "compare_student_groups",
                "inspect_item_quality",
                "inspect_discovery_support",
                "finish",
            ],
            "unsupported": [
                "five_dimension_inference_from_questions",
                "combined_ability_grade",
                "ai_collaboration_ability",
                "learning_growth",
                "causal_teaching_effect",
                "score_correctness_judgment",
            ],
            "synthetic_only": True,
        }
