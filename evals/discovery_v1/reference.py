"""Independent Fraction/Decimal oracle. MUST NOT import any app/SUT function."""

import hashlib
import json
from collections import Counter
from dataclasses import asdict, replace
from decimal import Decimal, localcontext
from fractions import Fraction as F
from operator import eq, ge, gt, le, lt

from .contracts import DIMS, Binding, Case, Predicate, Query, Reference, evidence, identity

OPS = {"<": lt, "<=": le, "==": eq, ">=": ge, ">": gt}


def wire(value: object) -> object:
    if isinstance(value, F):
        return {"numerator": value.numerator, "denominator": value.denominator}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, dict):
        return {str(k): wire(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire(v) for v in value]
    return value


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(wire(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def case_digest(case: Case) -> str:
    return digest(asdict(case))


def score(raw: object) -> F | None:
    if type(raw) is int:
        value = F(raw)
    elif isinstance(raw, Decimal) and raw.is_finite():
        value = F(raw)
    else:
        return None
    return value if 1 <= value <= 5 and (value * 100).denominator == 1 else None


def _get(case: Case, i: int, dim: str) -> F | None:
    return score(case.rows[i][DIMS.index(dim)])


def _bindings(case: Case, indexes, dimensions) -> tuple[Binding, ...]:
    return tuple(
        Binding(identity(case, i), tuple(evidence(case, i, d) for d in dimensions))
        for i in sorted(indexes)
    )


def _median(values: list[F]) -> F | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _stats(case: Case, indexes, dim: str) -> tuple[dict[str, object], tuple[int, ...]]:
    valid = tuple(i for i in indexes if _get(case, i, dim) is not None)
    values = [_get(case, i, dim) for i in valid]
    counts = Counter(values)
    missing = sum(case.rows[i][DIMS.index(dim)] is None for i in indexes)
    return {
        "valid": len(valid),
        "missing": missing,
        "invalid": len(indexes) - len(valid) - missing,
        "mean": sum(values, F(0)) / len(values) if values else None,
        "median": _median(values),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "distribution": tuple((v, counts[v], len(values)) for v in sorted(counts)),
    }, valid


def _check(conditions: tuple[Predicate, ...]) -> None:
    if not 1 <= len(conditions) <= 2 or len({p.dimension for p in conditions}) != len(conditions):
        raise ValueError("invalid_conditions")
    for p in conditions:
        if p.dimension not in DIMS or p.operator not in OPS or not isinstance(p.threshold, str):
            raise ValueError("invalid_condition")
        value = F(p.threshold)
        if not 1 <= value <= 5 or (value * 100).denominator != 1:
            raise ValueError("invalid_threshold")


def _select(case: Case, conditions: tuple[Predicate, ...]):
    _check(conditions)
    matches = []
    unknown = []
    rest = []
    for i in range(len(case.rows)):
        if any(_get(case, i, p.dimension) is None for p in conditions):
            unknown.append(i)
        elif all(OPS[p.operator](_get(case, i, p.dimension), F(p.threshold)) for p in conditions):
            matches.append(i)
        else:
            rest.append(i)
    return tuple(matches), tuple(rest), tuple(unknown)


def _ranks(values: list[F]) -> list[F]:
    # Independent order-statistic definition, not the SUT ranking algorithm.
    return [
        F(
            2 * sum(other < value for other in values)
            + sum(other == value for other in values)
            + 1,
            2,
        )
        for value in values
    ]


def _rho(left: list[F], right: list[F]) -> Decimal | None:
    if len(left) < 5 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    x, y = _ranks(left), _ranks(right)
    if len(x) < 5:
        return None
    xm, ym = sum(x) / len(x), sum(y) / len(y)
    xx = sum((v - xm) ** 2 for v in x)
    yy = sum((v - ym) ** 2 for v in y)
    if not xx or not yy:
        return None
    xy = sum((a - xm) * (b - ym) for a, b in zip(x, y, strict=True))

    def decimal(v):
        return Decimal(v.numerator) / Decimal(v.denominator)

    with localcontext() as context:
        context.prec = 50
        return decimal(xy) / (decimal(xx) * decimal(yy)).sqrt()


def reference(case: Case, q: Query) -> Reference:
    n = len(case.rows)
    sources = {}
    reasons = []
    signature = [case.batch_id, case.snapshot_id, q.kind]
    if q.kind in ("relation", "rare"):
        if q.left not in DIMS or q.right not in DIMS or q.left == q.right:
            raise ValueError("invalid_dimensions")
        pairs = tuple(
            i
            for i in range(n)
            if _get(case, i, q.left) is not None and _get(case, i, q.right) is not None
        )
        left = [_get(case, i, q.left) for i in pairs]
        right = [_get(case, i, q.right) for i in pairs]
        if q.kind == "relation":
            rho = _rho(left, right)
            state = (
                "insufficient_pairs"
                if len(pairs) < 5
                else "constant_input"
                if rho is None
                else "computable"
            )
            values = {
                "paired_count": len(pairs),
                "excluded_count": n - len(pairs),
                "state": state,
                "rho": rho,
            }
            sources["paired_scores"] = _bindings(case, pairs, (q.left, q.right))
            qualified = rho is not None and abs(rho) >= Decimal("0.6")
            signature.append(sorted((q.left, q.right)))
            reasons.append(
                "qualified_relation"
                if qualified
                else state
                if rho is None
                else "below_effect_threshold"
            )
        else:
            differences = [a - b for a, b in zip(left, right, strict=True)]
            ordered = sorted(differences)
            if ordered:
                q1 = ordered[(len(ordered) + 3) // 4 - 1]
                q3 = ordered[(3 * len(ordered) + 3) // 4 - 1]
                center = _median(ordered)
                iqr = q3 - q1
                lower, upper = q1 - F(3, 2) * iqr, q3 + F(3, 2) * iqr
                selected = tuple(
                    i
                    for i, d in zip(pairs, differences, strict=True)
                    if (d < lower if q.side == "lower" else d > upper)
                    and abs(d - center) >= F(1, 2)
                )
            else:
                q1 = q3 = center = lower = upper = None
                selected = ()
            if q.side not in ("lower", "upper"):
                raise ValueError("invalid_rare_side")
            values = {
                "pair_count": len(pairs),
                "excluded_count": n - len(pairs),
                "q1": q1,
                "q3": q3,
                "median": center,
                "lower_fence": lower,
                "upper_fence": upper,
                "match_count": len(selected),
                "distribution": tuple(sorted(Counter(differences).items())),
                "pattern": "individual_review"
                if len(selected) == 1
                else "small_group_review"
                if len(selected) < 5
                else "group_pattern",
            }
            sources["rare_members"] = _bindings(case, selected, (q.left, q.right))
            sources["pair_reference"] = _bindings(case, pairs, (q.left, q.right))
            qualified = (
                len(pairs) >= 5 and bool(selected) and F(len(selected), len(pairs)) <= F(1, 10)
            )
            signature += [sorted((q.left, q.right)), sorted(selected)]
            reasons.append("review_only_not_diagnosis" if qualified else "insufficient_or_not_rare")
    elif q.kind == "group":
        if q.outcome not in DIMS:
            raise ValueError("invalid_outcome")
        target, rest, unknown = _select(case, q.conditions)
        values = {
            "target_count": len(target),
            "remainder_count": len(rest),
            "unevaluable_count": len(unknown),
            "population_count": n,
            "eligible_count": n - len(unknown),
            "share_total": F(len(target), n) if n else None,
            "share_eligible": F(len(target), n - len(unknown)) if n - len(unknown) else None,
        }
        dims = tuple(p.dimension for p in q.conditions)
        sources["target_members"] = _bindings(case, target, dims)
        sources["remainder_members"] = _bindings(case, rest, dims)
        for dim in DIMS:
            a, aa = _stats(case, target, dim)
            b, bb = _stats(case, rest, dim)
            delta = (
                a["mean"] - b["mean"] if a["mean"] is not None and b["mean"] is not None else None
            )
            values[dim] = {
                "target": a,
                "remainder": b,
                "difference": delta,
                "condition_defined": dim in dims,
            }
            sources[f"target.{dim}"] = _bindings(case, aa, (dim,))
            sources[f"remainder.{dim}"] = _bindings(case, bb, (dim,))
        outcome = values[q.outcome]
        qualified = (
            q.outcome not in dims
            and outcome["difference"] is not None
            and min(outcome["target"]["valid"], outcome["remainder"]["valid"]) >= 5
            and abs(outcome["difference"]) >= F(1, 2)
        )
        signature += [sorted(dims), q.outcome, sorted((sorted(target), sorted(rest)))]
        reasons.append(
            "qualified_non_condition_outcome"
            if qualified
            else "condition_or_insufficient_support_effect"
        )
    elif q.kind == "boundary":
        _check(q.conditions)
        chosen = next((p for p in q.conditions if p.dimension == q.left), None)
        if chosen is None or chosen.operator not in ("<", "<="):
            raise ValueError("invalid_boundary")
        low = tuple(replace(p, operator="<") if p is chosen else p for p in q.conditions)
        high = tuple(replace(p, operator="<=") if p is chosen else p for p in q.conditions)
        a, _, u = _select(case, low)
        b, _, v = _select(case, high)
        assert u == v
        added = tuple(i for i in b if i not in a)
        observed = any(_get(case, i, q.left) == F(chosen.threshold) for i in range(n))
        values = {
            "strict_count": len(a),
            "inclusive_count": len(b),
            "added_count": len(added),
            "removed_count": len(set(a) - set(b)),
            "population_count": n,
            "eligible_count": n - len(u),
            "unevaluable_count": len(u),
            "observed_boundary": observed,
        }
        dims = tuple(p.dimension for p in q.conditions)
        sources = {
            "strict_members": _bindings(case, a, dims),
            "inclusive_members": _bindings(case, b, dims),
            "added_members": _bindings(case, added, dims),
        }
        qualified = observed and bool(added)
        signature.append(sorted((p.dimension, p.operator, str(F(p.threshold))) for p in high))
        reasons.append(
            "exact_definition_change_not_diagnosis"
            if qualified
            else "no_membership_change_or_insufficient"
        )
    else:
        raise ValueError("unsupported_fact_kind")
    return Reference(values, sources, bool(qualified), tuple(reasons), digest(signature))


def manifest(case: Case) -> dict[str, object]:
    targets = []
    for q in case.targets:
        expected = reference(case, q)
        targets.append(
            {
                "query": asdict(q),
                "values": wire(expected.values),
                "qualified": expected.qualified,
                "qualification_reasons": expected.reasons,
                "signature": expected.signature,
                "sources": {
                    k: {"count": len(v), "digest": digest([asdict(x) for x in v])}
                    for k, v in expected.bindings.items()
                },
            }
        )
    return {
        "case_id": case.case_id,
        "seed": case.seed,
        "recipe": case.recipe,
        "objective": case.objective,
        "student_count": len(case.rows),
        "distractor_count": len(case.distractors),
        "dataset_digest": case_digest(case),
        "targets": targets,
        "forbidden": case.forbidden,
        "allowed_stops": case.allowed_stops,
    }
