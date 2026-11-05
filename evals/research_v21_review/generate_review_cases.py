"""Independent synthetic fixture authoring; no project imports or SUT execution."""

import hashlib
import json
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "inputs"
ANSWERS = ROOT / "answers"


def rat(v):
    v = F(v)
    return str(v.numerator) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"


def source(item, rows, kind="score"):
    return {"item_id": item, "row_indices_1_based": rows, "evidence_kind": kind}


def stats(case, item):
    col = [x["item_id"] for x in case["items"]].index(item)
    ids = [i + 1 for i, row in enumerate(case["rows"]) if row[col] is not None]
    vals = sorted(F(case["rows"][i - 1][col]) for i in ids)
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    missing = [i + 1 for i, row in enumerate(case["rows"]) if row[col] is None]
    return {
        "denominator": len(case["rows"]),
        "valid_count": n,
        "missing_count": len(missing),
        "sum": rat(sum(vals)),
        "mean": rat(sum(vals) / n),
        "median": rat(med),
        "distribution": [{"score": rat(v), "count": vals.count(v)} for v in sorted(set(vals))],
        "valid_source": source(item, ids),
        "missing_declaration_source": source(item, missing, "row_declaration"),
    }


def group(case, conditions, outcome):
    idx = {x["item_id"]: i for i, x in enumerate(case["items"])}
    target, rest, unknown = [], [], []
    for i, row in enumerate(case["rows"], 1):
        if any(row[idx[k]] is None for k in conditions):
            unknown.append(i)
        elif all(F(row[idx[k]]) == F(v) for k, v in conditions.items()):
            target.append(i)
        else:
            rest.append(i)

    def summary(ids):
        valid = [i for i in ids if case["rows"][i - 1][idx[outcome]] is not None]
        vals = [F(case["rows"][i - 1][idx[outcome]]) for i in valid]
        return {
            "count": len(ids),
            "outcome_valid_count": len(vals),
            "mean": rat(sum(vals) / len(vals)) if vals else None,
            "outcome_source": source(outcome, valid),
            "condition_sources": [source(k, ids) for k in conditions],
        }

    a, b = summary(target), summary(rest)
    return {
        "conditions_all_equal": conditions,
        "outcome": outcome,
        "denominator": len(case["rows"]),
        "unevaluable_count": len(unknown),
        "target": a,
        "remainder": b,
        "difference": rat(F(a["mean"]) - F(b["mean"])) if a["mean"] and b["mean"] else None,
    }


def ranks(vals):
    return [F(1 + sum(w < v for w in vals) + sum(w <= v for w in vals), 2) for v in vals]


def independent_rank_moments(case, left, right):
    idx = {x["item_id"]: i for i, x in enumerate(case["items"])}
    ids = [
        i + 1
        for i, r in enumerate(case["rows"])
        if r[idx[left]] is not None and r[idx[right]] is not None
    ]
    x, y = ([F(case["rows"][i - 1][idx[k]]) for i in ids] for k in (left, right))
    mid = F(len(ids) + 1, 2)
    rx, ry = ([v - mid for v in ranks(z)] for z in (x, y))
    return ids, (
        sum(v * v for v in rx),
        sum(v * v for v in ry),
        sum(a * b for a, b in zip(rx, ry, strict=False)),
    )


def relation(case, left, right, manual_moments, rho):
    ids, actual = independent_rank_moments(case, left, right)
    assert actual == tuple(F(x) for x in manual_moments), (case["case_id"], left, right, actual)
    return {
        "left": left,
        "right": right,
        "paired_count": len(ids),
        "excluded_count": len(case["rows"]) - len(ids),
        "centered_rank_ss_left": rat(actual[0]),
        "centered_rank_ss_right": rat(actual[1]),
        "centered_rank_cross_product": rat(actual[2]),
        "spearman_rho_exact": rho,
        "sources": [source(left, ids), source(right, ids)],
    }


def case(case_id, kind, scale, labels, rows):
    prefix = "I" if kind == "competency" else "Q"
    return {
        "case_id": case_id,
        "synthetic_only": True,
        "measurement_level": "ordinal",
        "kind": kind,
        "scale": dict(zip(("min", "max", "step"), scale, strict=False)),
        "minimum_disclosure_group": 5,
        "items": [
            {"item_id": f"{prefix}{i:02d}", "label": label, "rubric": None}
            for i, label in enumerate(labels, 1)
        ],
        "row_indexing": (
            "1-based participant index; columns follow items; null is missing, never zero"
        ),
        "rows": rows,
    }


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    INPUTS.mkdir(exist_ok=True)
    ANSWERS.mkdir(exist_ok=True)
    a = case(
        "review_corr_scope_20261103",
        "competency",
        ("1", "5", "1"),
        ["步骤组织", "步骤复核", "表达展开", "限制识别"],
        [
            [str(x), str(x), str(z), str(6 - x)]
            for x in (1, 2, 4, 5)
            for z in (1, 5)
            for _ in range(5)
        ],
    )
    b = case(
        "review_missing_support_20261103",
        "question",
        ("1", "5", "1"),
        ["题目甲", "题目乙", "题目丙", "题目丁"],
        [
            [
                str(1 + 2 * (i // 10)),
                str(1 + 2 * (i // 10)) if i < 20 else None,
                "3",
                "5" if i < 5 else None,
            ]
            for i in range(30)
        ],
    )
    c = case(
        "review_decimal_interaction_20261103",
        "question",
        ("0", "10", "0.5"),
        ["条件甲", "条件乙", "结果丙", "参照丁"],
        [
            [str(x), str(y), "2.5" if x == 0 and y == 0 else "7.5", "5"]
            for x, y in ((0, 0), (0, 10), (10, 0), (10, 10))
            for _ in range(10)
        ],
    )
    answers = [
        {
            "case_id": a["case_id"],
            "item_statistics": {i: stats(a, i) for i in ("I01", "I02", "I03", "I04")},
            "relations": [
                relation(a, "I01", "I02", (5000, 5000, 5000), "1"),
                relation(a, "I01", "I03", (5000, 4000, 0), "0"),
                relation(a, "I01", "I04", (5000, 5000, -5000), "-1"),
            ],
            "groups": [group(a, {"I03": "1"}, "I01")],
            "supported": [
                "I01与I02在本合成样本中完全同秩；I01与I04完全反秩。",
                "I03与I01的秩相关为0；I03=1组与其余组的I01均值同为3。",
            ],
            "unsupported": [
                "一次发现I01/I02关联即可宣称其他项已调查充分。",
                "存在因果方向、教学效果、个人能力定论、真实群体代表性。",
                "零秩相关等同于一般意义上的统计独立（这里只能依额外分块设计确认本表独立）。",
            ],
        },
        {
            "case_id": b["case_id"],
            "item_statistics": {i: stats(b, i) for i in ("Q01", "Q02", "Q03", "Q04")},
            "relations": [
                relation(b, "Q01", "Q02", (500, 500, 500), "1"),
                relation(b, "Q01", "Q03", (2000, 0, 0), None),
                relation(b, "Q01", "Q04", (0, 0, 0), None),
            ],
            "groups": [group(b, {"Q01": "1"}, "Q02"), group(b, {"Q01": "1"}, "Q04")],
            "supported": [
                "Q01/Q02只在20个有配对分数的行上相关为1；另10行Q02缺失。",
                "Q04只有5/30个分数，均值5仅描述这5行，25个缺失值不能替换为0。",
                "Q03为常数；Q01/Q04的5个配对中两列均为常数，相关系数均无定义。",
                "Q01=1组的Q04可用人数5，其余组为0，均值差不可计算。",
            ],
            "unsupported": [
                "Q01/Q02相关代表全部30人。",
                "Q04表现优秀且覆盖全体，或Q04其余人的表现差。",
                "把少于5人的进一步细分展示给模型/用户，或根据5人明细推断个人结论。",
                "当前完整完成了可依赖更多评分证据的调查。",
            ],
        },
        {
            "case_id": c["case_id"],
            "item_statistics": {i: stats(c, i) for i in ("Q01", "Q02", "Q03", "Q04")},
            "relations": [
                relation(c, "Q01", "Q02", (4000, 4000, 0), "0"),
                relation(c, "Q01", "Q03", (4000, 3000, 2000), "1/sqrt(3)"),
                relation(c, "Q02", "Q03", (4000, 3000, 2000), "1/sqrt(3)"),
            ],
            "groups": [group(c, {"Q01": "0"}, "Q03"), group(c, {"Q01": "0", "Q02": "0"}, "Q03")],
            "supported": [
                "Q01/Q02秩相关为0，但两者同时为0的10人结果均值2.5，其余30人为7.5，差为-5。",
                "只按Q01=0分组时结果均值5，对照组7.5，差为-2.5。",
                "两条件组合的描述性差异大于单条件差异；可作为继续核实的合成线索。",
            ],
            "unsupported": [
                "只因Q01/Q02无相关就排除组合差异。",
                "由组合差异推出两个条件具有因果交互效应、教学建议有效或统计显著。",
                "把量表均值当作合成能力总分。",
            ],
        },
    ]
    assert answers[0]["item_statistics"]["I01"]["mean"] == "3"
    assert answers[1]["item_statistics"]["Q02"]["mean"] == "2"
    assert answers[1]["item_statistics"]["Q04"]["missing_count"] == 25
    assert answers[2]["item_statistics"]["Q03"]["mean"] == "25/4"
    assert answers[2]["groups"][1]["difference"] == "-5"
    for obj, answer in zip((a, b, c), answers, strict=False):
        assert 20 <= len(obj["rows"]) <= 60 and 3 <= len(obj["items"]) <= 6
        lo, hi, step = (F(obj["scale"][k]) for k in ("min", "max", "step"))
        for row in obj["rows"]:
            assert len(row) == len(obj["items"])
            assert all(
                v is None or lo <= F(v) <= hi and ((F(v) - lo) / step).denominator == 1 for v in row
            )
        for st in answer["item_statistics"].values():
            assert st["valid_count"] >= 5
            assert all(cell["count"] >= 5 for cell in st["distribution"])
        for rel in answer["relations"]:
            assert rel["paired_count"] >= 5
        for result in answer["groups"]:
            for side in ("target", "remainder"):
                assert result[side]["count"] >= 5
                assert (
                    result[side]["outcome_valid_count"] == 0
                    or result[side]["outcome_valid_count"] >= 5
                )
        dump(INPUTS / (obj["case_id"] + ".json"), obj)
        answer["oracle_method"] = (
            "Independent Fraction arithmetic and analytic tied-rank moments; no SUT imports/calls"
        )
        answer["source_contract"] = (
            "1-based input row indices, item column and evidence kind; "
            "missing cells only cite row declarations"
        )
        dump(ANSWERS / (obj["case_id"] + ".json"), answer)
    input_bundle = []
    notes = [
        (
            "Fixed blocks: I01 in (1,2,4,5), I02=I01, I04=6-I01; within "
            "each block I03=1 or 5, 5 rows each. No randomness."
        ),
        (
            "For i=0..29: Q01=1+2*(i//10); Q02=Q01 only i<20; Q03=3; "
            "Q04=5 only i<5; otherwise null. No randomness."
        ),
        (
            "Blocks (Q01,Q02)=(0,0),(0,10),(10,0),(10,10), 10 rows each; "
            "Q03=2.5 in first block else 7.5; Q04=5. No randomness."
        ),
    ]
    for obj, note in zip((a, b, c), notes, strict=False):
        input_bundle.append(
            {
                "case_id": obj["case_id"],
                "kind": obj["kind"],
                "scale": obj["scale"],
                "items": [{"item_id": x["item_id"], "label": x["label"]} for x in obj["items"]],
                "rows": obj["rows"],
                "seed": 0,
                "generation_description": note,
            }
        )
    for answer in answers:
        answer["qualification_conditions"] = {
            "measurement_level": "ordinal",
            "minimum_disclosure_group": 5,
            "relation_defined": (
                "Both paired rank sums of squares must be nonzero; otherwise "
                "rho is null, never zero."
            ),
            "group_difference_defined": (
                "Both outcome valid groups must have at least 5 rows before "
                "disclosure; no missing-value substitution."
            ),
            "policy_qualification_or_rank": (
                "Not asserted: evaluate under actual system policy; "
                "arithmetic truth alone does not establish investigation "
                "completion."
            ),
        }
    dump(ROOT / "inputs.json", input_bundle)
    dump(ROOT / "oracles.json", answers)
    files = sorted(
        [
            *INPUTS.glob("*.json"),
            *ANSWERS.glob("*.json"),
            ROOT / "inputs.json",
            ROOT / "oracles.json",
        ]
    )
    manifest = {
        str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
    }
    dump(ROOT / "manifest.sha256.json", manifest)
    print(
        "PASS: 3 cases; sizes 40/30/40 x 4; exact hand-derived rank "
        "moments; source indices; scale grid; disclosure counts."
    )
    print("No strategy, Kernel, discovery, agent, paid model, or existing Oracle was executed.")


if __name__ == "__main__":
    main()
