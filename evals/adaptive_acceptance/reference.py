"""Independent arithmetic and fixture specification. Never imports app, Planner or Kernel."""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction as F
from operator import eq, ge, gt, le, lt

from evals.discovery_v1.contracts import DIMS, Case, Predicate, Query
from evals.discovery_v1.reference import digest, reference

VERSION = "adaptive-acceptance-0.1"
OPS = {"<": lt, "<=": le, "==": eq, ">=": ge, ">": gt}


@dataclass(frozen=True)
class Spec:
    key: str
    items: tuple[str, ...]
    rows: tuple[tuple[F | None, ...], ...]
    scales: tuple[tuple[F, F, F] | None, ...]
    absent: tuple[str, ...] = ()
    rubric: tuple[str, ...] = ()
    frozen: Case | None = None
    seed: int = 0

    @property
    def fingerprint(self):
        return digest([self.items, self.rows, self.scales, self.absent, self.rubric])

    def state(self, i, item):
        j = self.items.index(item)
        if item in self.absent:
            return "evidence_absent"
        raw = self.rows[i][j]
        if raw is None:
            return "missing"
        scale = self.scales[j]
        if scale is None:
            return "range_unknown"
        low, high, step = scale
        return (
            "valid" if low <= raw <= high and ((raw - low) / step).denominator == 1 else "invalid"
        )

    def valid(self, item, scope=None):
        indexes = range(len(self.rows)) if scope is None else scope
        j = self.items.index(item)
        return {
            i: self.rows[i][j] for i in indexes if self.state(i, item) in ("valid", "range_unknown")
        }

    def stats(self, item, scope=None):
        indexes = tuple(range(len(self.rows)) if scope is None else scope)
        data = self.valid(item, indexes)
        ordered = sorted(data.values())
        n = len(ordered)
        return {
            "denominator": len(indexes),
            "valid_count": n,
            "excluded_count": len(indexes) - n,
            "states": dict(Counter(self.state(i, item) for i in indexes)),
            "mean": sum(ordered, F(0)) / n if n else None,
            "median": (ordered[(n - 1) // 2] + ordered[n // 2]) / 2 if n else None,
            "range_checked": self.scales[self.items.index(item)] is not None,
        }


def frozen_spec(case: Case) -> Spec:
    def score(v):
        if v is None:
            return None
        try:
            return F(v)
        except (ValueError, TypeError):
            return F(99999)

    return Spec(
        case.case_id,
        tuple(f"I{i:02}" for i in range(1, 6)),
        tuple(tuple(score(v) for v in row) for row in case.rows),
        ((F(1), F(5), F(1, 100)),) * 5,
        frozen=case,
    )


def question_spec(recipe: str) -> Spec:
    """Seed0 fixed formula, independently written; not reading the SUT ZIP or its generator."""
    n = 3 if recipe == "small" else 0 if recipe == "empty" else 20
    m = 3 if recipe == "no_evidence" else 13
    items = tuple(f"Q{j:02}" for j in range(1, m + 1))
    rows = []
    for i in range(n):
        row = []
        for j in range(1, m + 1):
            v = F(80 if (i < n // 2) == (j % 2 == 1) else 20)
            if recipe in ("contest13", "limited") and j == m and i >= n - 5:
                v = None
            if recipe == "privacy20" and ((j == 1 and i < 5) or (j == 2 and i < 6)):
                v = None
            row.append(v)
        rows.append(tuple(row))
    return Spec(
        recipe,
        items,
        tuple(rows),
        tuple(
            None if recipe == "limited" and j == m - 1 else (F(0), F(100), F(1, 100))
            for j in range(1, m + 1)
        ),
        (items[-3],) if recipe in ("limited", "no_evidence") else (),
        (items[0],),
    )


def relation(spec, left, right):
    if left == right or any(spec.scales[spec.items.index(i)] is None for i in (left, right)):
        raise ValueError("relation_scale_or_level_unknown")
    a, b = spec.valid(left), spec.valid(right)
    ids = tuple(sorted(a.keys() & b.keys()))
    x, y = [a[i] for i in ids], [b[i] for i in ids]

    def ranks(values):
        return [
            F(2 * sum(z < v for z in values) + sum(z == v for z in values) + 1, 2) for v in values
        ]

    state = (
        "insufficient_pairs"
        if len(ids) < 5
        else "constant_input"
        if len(set(x)) < 2 or len(set(y)) < 2
        else "computable"
    )
    rho = None
    if state == "computable":
        x, y = ranks(x), ranks(y)
        xm, ym = sum(x) / len(x), sum(y) / len(y)
        xx, yy = sum((v - xm) ** 2 for v in x), sum((v - ym) ** 2 for v in y)
        xy = sum((u - xm) * (v - ym) for u, v in zip(x, y, strict=True))
        with localcontext() as ctx:
            ctx.prec = 50

            def dec(f):
                return Decimal(f.numerator) / Decimal(f.denominator)

            rho = dec(xy) / (dec(xx) * dec(yy)).sqrt()
    return (
        {
            "left": left,
            "right": right,
            "rho": rho,
            "state": state,
            "paired_count": len(ids),
            "excluded_count": len(spec.rows) - len(ids),
            "denominator": len(spec.rows),
        },
        {"left.paired": (left, ids, "score"), "right.paired": (right, ids, "score")},
        rho is not None and abs(rho) >= Decimal("0.6"),
    )


def group(spec, conditions, outcome):
    items = [p["item_id"] for p in conditions]
    if outcome in items or len(set(items)) != len(items):
        raise ValueError("distinct_condition_and_outcome_items_required")
    for item in (*items, outcome):
        if spec.scales[spec.items.index(item)] is None:
            raise ValueError("filter_or_outcome_scale_unknown")
    for p in conditions:
        lo, hi, step = spec.scales[spec.items.index(p["item_id"])]
        threshold = F(p["threshold"])
        if not lo <= threshold <= hi or ((threshold - lo) / step).denominator != 1:
            raise ValueError("threshold_outside_item_scale")
    available = {item: spec.valid(item) for item in items}
    eligible = tuple(
        i for i in range(len(spec.rows)) if all(i in available[item] for item in items)
    )
    target = tuple(
        i
        for i in eligible
        if all(
            OPS[p["operator"]](available[p["item_id"]][i], F(p["threshold"])) for p in conditions
        )
    )
    rest = tuple(i for i in eligible if i not in target)
    unknown = tuple(i for i in range(len(spec.rows)) if i not in eligible)
    a, b = spec.stats(outcome, target), spec.stats(outcome, rest)
    delta = a["mean"] - b["mean"] if a["mean"] is not None and b["mean"] is not None else None
    span = spec.scales[spec.items.index(outcome)][1] - spec.scales[spec.items.index(outcome)][0]
    values = {
        "outcome": outcome,
        "conditions": conditions,
        "target_count": len(target),
        "remainder_count": len(rest),
        "unevaluable_count": len(unknown),
        "denominator": len(spec.rows),
        "eligible_denominator": len(eligible),
        "share_total": F(len(target), len(spec.rows)) if spec.rows else None,
        "share_eligible": F(len(target), len(eligible)) if eligible else None,
        "target_valid": a["valid_count"],
        "remainder_valid": b["valid_count"],
        "target_mean": a["mean"],
        "remainder_mean": b["mean"],
        "difference": delta,
        "outcome_unit": "level" if spec.items[0].startswith("I") else "points",
    }
    sources = {
        "target.valid": (outcome, tuple(spec.valid(outcome, target)), "score"),
        "remainder.valid": (outcome, tuple(spec.valid(outcome, rest)), "score"),
        "unevaluable.rows": (items[0], unknown, "row"),
    }
    for item in items:
        sources["target.condition." + item] = (item, target, "score")
        sources["remainder.condition." + item] = (item, rest, "score")
    qualified = (
        min(a["valid_count"], b["valid_count"]) >= 5
        and delta is not None
        and abs(delta) >= span / 8
    )
    return values, sources, qualified


def quality(spec, item):
    s = spec.stats(item)
    sources = {
        "state." + state: (
            item,
            tuple(i for i in range(len(spec.rows)) if spec.state(i, item) == state),
            "row",
        )
        for state in s["states"]
    }
    values = {
        "item_id": item,
        "denominator": len(spec.rows),
        "counts": s["states"],
        "scale_known": spec.scales[spec.items.index(item)] is not None,
        "rubric_available": item in spec.rubric,
        "score_changed": False,
    }
    return (
        values,
        sources,
        len(spec.rows) >= 5 and (any(k != "valid" for k in s["states"]) or item not in spec.rubric),
    )


def expectation(spec: Spec, tool: str, args: dict) -> tuple[dict, dict, bool]:
    if tool == "query_dimension_relation":
        return relation(spec, args["left"], args["right"])
    if tool == "compare_student_groups":
        return group(spec, args["conditions"], args["outcomes"][0])
    if tool == "inspect_item_quality":
        return quality(spec, args["item_id"])
    raise ValueError("not_a_discovery_query")


def frozen_query(spec, tool, args):
    def dim(item):
        return DIMS[spec.items.index(item)]

    if tool == "query_dimension_relation":
        return Query("relation", dim(args["left"]), dim(args["right"]))
    if tool == "compare_student_groups":
        return Query(
            "group",
            conditions=tuple(
                Predicate(dim(p["item_id"]), p["operator"], p["threshold"])
                for p in args["conditions"]
            ),
            outcome=dim(args["outcomes"][0]),
        )
    raise ValueError("outside_frozen_discovery_types")


def signature(spec, tool, args):
    if spec.frozen is not None:
        return reference(spec.frozen, frozen_query(spec, tool, args)).signature
    values, sources, qual = expectation(spec, tool, args)
    if tool == "query_dimension_relation":
        key = ["relation", sorted((args["left"], args["right"]))]
    elif tool == "compare_student_groups":
        key = [
            "group",
            sorted(p["item_id"] for p in args["conditions"]),
            args["outcomes"][0],
            sorted((sources["target.valid"][1], sources["remainder.valid"][1])),
        ]
    else:
        key = ["quality", args["item_id"]]
    return digest(key)


def target_queries(spec):
    if len(spec.rows) < 5:
        return []
    queries = [
        ("query_dimension_relation", {"left": "Q01", "right": "Q02"}),
        (
            "compare_student_groups",
            {
                "conditions": [{"item_id": "Q01", "operator": "<=", "threshold": "20"}],
                "outcomes": ["Q02"],
            },
        ),
    ]
    # Explicit independently specified review targets, not a path sent to the planner.
    if spec.key == "contest13":
        queries.append(("inspect_item_quality", {"item_id": "Q13"}))
    if spec.key == "limited":
        queries += [
            ("inspect_item_quality", {"item_id": "Q11"}),
            ("inspect_item_quality", {"item_id": "Q12"}),
            ("inspect_item_quality", {"item_id": "Q13"}),
        ]
    if spec.key == "no_evidence":
        queries = [
            ("inspect_item_quality", {"item_id": "Q01"}),
            (
                "compare_student_groups",
                {
                    "conditions": [{"item_id": "Q02", "operator": "<=", "threshold": "20"}],
                    "outcomes": ["Q03"],
                },
            ),
        ]
    return queries


def targets(spec: Spec) -> set[str]:
    if spec.frozen is not None:
        return {
            r.signature for q in spec.frozen.targets if (r := reference(spec.frozen, q)).qualified
        }
    result = set()
    for tool, args in target_queries(spec):
        try:
            if expectation(spec, tool, args)[2]:
                result.add(signature(spec, tool, args))
        except ValueError:
            continue
    return result


def question_manifest():
    """Versioned independent score anchors; never generated from SUT/Agent results."""
    recipes = (
        "contest13",
        "contest13_complete",
        "limited",
        "no_evidence",
        "small",
        "privacy20",
        "empty",
    )
    return {
        "version": VERSION,
        "seed": 0,
        "generation": "Seed0: odd/even80/20; base20/small3/empty0; explicit statuses",
        "cases": [
            {
                "recipe": recipe,
                "input_fingerprint": (s := question_spec(recipe)).fingerprint,
                "participant_count": len(s.rows),
                "items": list(s.items),
                "statistics": {item: s.stats(item) for item in s.items},
                "forbidden": [
                    "question_to_five_dimension_inference",
                    "cross_item_scale_sum",
                    "causal_claim",
                    "invented_rubric",
                ],
                "targets": sorted(targets(s)),
                "expected_queries": [
                    {
                        "tool": tool,
                        "arguments": args,
                        "values": expectation(s, tool, args)[0],
                        "qualified": expectation(s, tool, args)[2],
                        "source_scopes": {
                            k: {"item": v[0], "count": len(v[1]), "index_digest": digest(v[1])}
                            for k, v in expectation(s, tool, args)[1].items()
                        },
                    }
                    for tool, args in target_queries(s)
                    if tool != "query_dimension_relation"
                    or all(
                        s.scales[s.items.index(i)] is not None
                        for i in (args["left"], args["right"])
                    )
                ],
                "allowed_rejections": [
                    "unknown_scale",
                    "insufficient_sample",
                    "no_authorized_score_evidence",
                    "cumulative_disclosure_limit",
                ],
            }
            for recipe in recipes
        ],
    }
