"""Independent, versioned five-dimension fixtures. No files or reference answers."""

from decimal import Decimal
from io import BytesIO
from itertools import product
from random import Random

from app.analysis.edu.models import Dimension
from openpyxl import load_workbook

from scripts.edu_demo_fixtures import make_fixture

RECIPES = ("profile-balanced-72", "profile-overlap-96", "profile-no-pattern-81")
RECIPE_ID_OFFSETS = dict(zip(RECIPES, (100_000, 200_000, 300_000), strict=True))


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


def workbook(recipe: str) -> bytes:
    """Render a registered recipe through the formal V1 workbook writer."""
    spec = specification(recipe)
    original = make_fixture(len(spec["rows"]), spec["seed"])
    book = load_workbook(BytesIO(original.data))
    for index, values in enumerate(spec["rows"]):
        public_id = RECIPE_ID_OFFSETS[recipe] + index + 1
        book["Sheet2"].cell(2 + index * 6, 1).value = (
            f"{public_id:012d} 画像合成{recipe}-{index + 1}"
        )
        for dimension, value in enumerate(values):
            book["Sheet2"].cell(3 + index * 6 + dimension, 3).value = Decimal(value)
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()
