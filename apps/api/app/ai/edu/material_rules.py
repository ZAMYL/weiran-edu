"""Local DS-04B rules over the reviewed structural projection."""

_HEADERS = {
    "考号": "student_key",
    "学号": "student_key",
    "participant id": "student_key",
    "题号": "problem_key",
    "question key": "problem_key",
    "源代码": "code",
    "code": "code",
    "得分": "score",
    "成绩": "score",
    "判题": "verdict",
    "verdict": "verdict",
    "测试点": "test_point",
    "测试结果": "test_result",
    "运行时间": "runtime",
    "维度": "dimension",
    "dimension": "dimension",
    "维度评分": "dimension_score",
    "评分依据": "rubric",
}


def rule_suggestions(overview: dict) -> dict:
    """Small exact-header comparator, using the same safe view; never confirms."""
    roles, fields, keys = [], [], []
    for table in overview["tables"]:
        found = set()
        for col in table["columns"]:
            if len(fields) == 360:
                break  # Same limit as Suggestions; remaining columns stay unresolved.
            ref = col["ref"]
            meaning = _HEADERS.get(" ".join(col["header_tokens"]))
            if meaning is None or not col["present"] or col["blocked"] or table["blocked"]:
                continue
            found.add(meaning)
            fields.append({"column": ref, "meaning": meaning, "evidence": [ref]})
            if meaning == "student_key":
                keys.append((table["file"], ref, col["present"] == col["distinct"]))
        role = (
            "evaluation"
            if {"student_key", "dimension_score"} <= found
            else "programming"
            if {"student_key", "problem_key"} <= found
            and found & {"code", "score", "verdict", "test_result"}
            else None
        )
        if role:
            ref = table["ref"]
            roles.append({"table": ref, "role": role, "evidence": [ref]})
    links = []
    for i, (file, left, unique) in enumerate(keys):
        for other, right, other_unique in keys[i + 1 :]:
            if file == other or not (unique or other_unique):
                continue
            if other_unique:
                lref, rref = left, right
            else:
                lref, rref = right, left
            links.append(
                {
                    "left": lref,
                    "right": rref,
                    "evidence": [lref, rref],
                    "cardinality": "one_to_one" if unique and other_unique else "many_to_one",
                }
            )
            if len(links) == 40:
                return {"roles": roles, "fields": fields, "links": links}
    return {"roles": roles, "fields": fields, "links": links}
