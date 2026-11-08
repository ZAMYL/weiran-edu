"""Independent, versioned five-dimension fixtures. No files or reference answers."""

from itertools import product
from random import Random

from app.analysis.edu.models import Dimension

RECIPES = ("profile-balanced-72", "profile-overlap-96", "profile-no-pattern-81")


def specification(recipe: str) -> dict:
    rows = []
    seed = dict(zip(RECIPES, (811, 821, 823), strict=True))[recipe]
    if recipe == RECIPES[0]:
        for base in ((2, 4, 3, 3, 4), (4, 2, 4, 3, 2), (3, 3, 2, 3, 3)):
            for i in range(24):
                jitter = (-25, 0, 25)[i % 3]
                rows.append([f"{(v * 100 + jitter) / 100:.2f}" for v in base])
    elif recipe == RECIPES[1]:
        for a, b in product((0, 1), repeat=2):
            for i in range(24):
                jitter = (-10, 0, 10)[i % 3]
                rows.append(
                    [
                        str(2 if a == 0 else 4),
                        "2.25" if b == 0 else "4.25",
                        f"{((4 if a == 0 else 2) * 100 + jitter) / 100:.2f}",
                        f"{((4 if b == 0 else 2) * 100 + jitter) / 100:.2f}",
                        f"{((4 if a == b == 0 else 3) * 100 + jitter) / 100:.2f}",
                    ]
                )
    elif recipe == RECIPES[2]:
        levels = ("2.75", "3", "3.25")
        for values in product(range(3), repeat=4):
            rows.append([levels[v] for v in (*values, sum(values) % 3)])
    Random(seed).shuffle(rows)
    return {
        "protocol": "profile-synthetic-v1",
        "seed": seed,
        "kind": "competency",
        "item_names": {f"I{i + 1:02}": d.value for i, d in enumerate(Dimension)},
        "minimum": "1",
        "maximum": "5",
        "step": "0.01",
        "rows": rows,
    }
