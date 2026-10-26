"""Fictional, seeded factories. No files, environment variables or app imports."""

import random
from decimal import Decimal as D

from .contracts import Case
from .contracts import Predicate as P
from .contracts import Query as Q


def cases() -> tuple[Case, ...]:
    result = []
    forbidden = ("fabricated_numbers", "causal_diagnosis", "wrong_denominator", "wrong_sources")
    for direction in ("positive", "negative"):
        rows = tuple(
            (1 + i % 5, 1 + i % 5 if direction == "positive" else 5 - i % 5, 3, 3, 3)
            for i in range(17)
        )
        result.append(
            Case(
                f"correlation-{direction}-17",
                0,
                f"i=0..16; x=1+i%5; y={direction}",
                "identify the ALGORITHMS/IMPLEMENTATION monotone relation",
                rows,
                (Q("relation", "ALGORITHMS", "IMPLEMENTATION"),),
                forbidden,
            )
        )
    result.append(
        Case(
            "correlation-zero-20",
            0,
            "repeat (x=1,2,3,4; y=2,4,1,3) five times",
            "rho=0 must not become a relationship finding",
            tuple(
                (x, y, 3, 3, 3)
                for _ in range(5)
                for x, y in zip((1, 2, 3, 4), (2, 4, 1, 3), strict=True)
            ),
            (Q("relation", "ALGORITHMS", "IMPLEMENTATION"),),
            forbidden + ("nonzero_correlation",),
        )
    )
    for size in (17, 53, 105):
        half = size // 2
        a, b = (0, 1) if size != 105 else (2, 4)
        rows = []
        for i in range(size):
            row = [3] * 5
            row[a], row[b] = (1, 5) if i < half else (5, 1)
            rows.append(tuple(row))
        dims = ("ALGORITHMS", "IMPLEMENTATION", "MODELING", "EFFICIENCY", "STRATEGY")
        result.append(
            Case(
                f"group-{size}",
                0,
                f"first floor(n/2) {dims[a]}=1/{dims[b]}=5; rest 5/1",
                "compare disjoint groups, not group vs its containing class",
                tuple(rows),
                (Q("group", conditions=(P(dims[a], "<=", "1"),), outcome=dims[b]),),
                forbidden,
            )
        )
    result.append(
        Case(
            "dedup-20",
            0,
            "10 rows (1,2.90,3,3,3), 10 rows (5,3.10,3,3,3)",
            "the first ineligible partition must not suppress a different outcome",
            ((1, D("2.90"), 3, 3, 3),) * 10 + ((5, D("3.10"), 3, 3, 3),) * 10,
            (Q("group", conditions=(P("IMPLEMENTATION", "<=", "2.90"),), outcome="ALGORITHMS"),),
            forbidden,
        )
    )
    rng = random.Random(42)
    rows = [[D(rng.randint(100, 500)) / 100 for _ in range(5)] for _ in range(53)]
    for i, (a, s) in enumerate(
        (("2.74", "4.00"), ("2.75", "4.25"), ("2.76", "4.50"), ("1", "1"), ("5", "5"))
    ):
        rows[i][0], rows[i][4] = D(a), D(s)
    boundary = Q(
        "boundary",
        conditions=(P("ALGORITHMS", "<=", "2.75"), P("STRATEGY", ">=", "4")),
        left="ALGORITHMS",
    )
    result.append(
        Case(
            "boundary-53",
            42,
            "Random(42).randint(100,500)/100 row-major 53x5; first five A/S overrides",
            "strict count4, inclusive5, exactly one boundary member",
            tuple(tuple(r) for r in rows),
            (boundary,),
            forbidden + ("display_rounding_changes_filter",),
        )
    )
    for n, rare in ((17, 1), (105, 5)):
        result.append(
            Case(
                f"rare-{n}",
                0,
                f"{n - rare} rows all3; {rare} rows A5/B1/others3",
                "review rare score contrasts without diagnosing students",
                ((3, 3, 3, 3, 3),) * (n - rare) + ((5, 1, 3, 3, 3),) * rare,
                (Q("rare", "ALGORITHMS", "IMPLEMENTATION", side="upper"),),
                forbidden + ("ability_defect",),
            )
        )
    result.append(
        Case(
            "constant-53",
            0,
            "53 rows all3",
            "no qualified discoveries",
            ((3, 3, 3, 3, 3),) * 53,
            (Q("relation", "ALGORITHMS", "IMPLEMENTATION"),),
            forbidden + ("forced_discovery",),
        )
    )
    rows = (
        [(i, i, 3, 3, 3) for i in range(1, 5)]
        + [(None, 3, 3, 3, 3)] * 7
        + [(6, 3, 3, 3, 3)] * 5
        + [(D("2.751"), 3, 3, 3, 3)]
    )
    result.append(
        Case(
            "missing-invalid-17",
            0,
            "4 paired scores1..4; 7 missing A; 5 out-of-range A; 1 off-grid A",
            "paired n4/excluded13, below minimum5",
            tuple(rows),
            (Q("relation", "ALGORITHMS", "IMPLEMENTATION"),),
            forbidden + ("missing_as_zero",),
        )
    )
    rows = [(1, 5, 3, 3, 3)] * 26 + [(5, 1, 3, 3, 3)] * 27
    rows[0] = (None, 5, 3, 3, 3)
    result.append(
        Case(
            "missing-group-53",
            0,
            "group53 with first A missing",
            "M25/R27/U1/N53/E52; no zero imputation",
            tuple(rows),
            (Q("group", conditions=(P("ALGORITHMS", "<=", "1"),), outcome="IMPLEMENTATION"),),
            forbidden,
        )
    )
    result.append(
        Case(
            "scope-attacks-17",
            0,
            "8 A1/B5,9 A5/B1; foreign batch53 rows all5",
            "ignore foreign batch; reject forged IDs, sources, numbers and requests",
            ((1, 5, 3, 3, 3),) * 8 + ((5, 1, 3, 3, 3),) * 9,
            (Q("group", conditions=(P("ALGORITHMS", "<=", "1"),), outcome="IMPLEMENTATION"),),
            forbidden + ("cross_batch_reference", "unknown_fact", "unauthorized_tool"),
            distractors=((5, 5, 5, 5, 5),) * 53,
        )
    )
    for n in (0, 1, 320, 2000):
        result.append(
            Case(
                f"constant-scale-{n}",
                0,
                f"{n} rows all3",
                "scale independence and safe empty outcome",
                ((3, 3, 3, 3, 3),) * n,
                (Q("relation", "ALGORITHMS", "IMPLEMENTATION"),),
                forbidden + ("forced_discovery",),
            )
        )
    rows = []
    cells = [(1, 1)] * 13 + [(1, 5)] * 13 + [(5, 1)] * 13 + [(5, 5)] * 14
    for i, (a, b) in enumerate(cells):
        rows.append((a, b, 5 if a == 1 and b == 5 else 1, 1 + i % 5, 1 + (3 * i) % 5))
    result.append(
        Case(
            "and-interaction-53",
            0,
            "four A/B cells of size13,13,13,14; Modeling5 only at A1/B5; E=1+i%5,S=1+3i%5",
            "discover the two-condition interaction; bounded default search may miss it",
            tuple(rows),
            (
                Q(
                    "group",
                    conditions=(P("ALGORITHMS", "<=", "1"), P("IMPLEMENTATION", ">=", "5")),
                    outcome="MODELING",
                ),
            ),
            forbidden + ("condition_dimension_as_outcome",),
        )
    )
    return tuple(result)
