"""Bounded, data-derived investigation frontier, not a claim of exhaustive analysis."""

import json
from fractions import Fraction
from hashlib import sha256

VERSION = "research-completeness-0.1"
MAX_DIRECTIONS = 4


def query_key(name: str, args: dict) -> str:
    args = json.loads(json.dumps(args))
    if name == "query_dimension_relation":
        args = {"items": sorted((args["left"], args["right"]))}
    if name == "compare_student_groups":
        args["conditions"] = sorted(
            [
                (p["item_id"], p["operator"], str(Fraction(p["threshold"])))
                for p in args["conditions"]
            ]
        )
    return json.dumps([name, args], sort_keys=True)


def assess(goal, executor, *, tools_remaining: int, models_remaining: int) -> dict:
    facts = list(executor.facts.values())
    catalog = {i["item_id"]: i for i in executor.kernel.catalog()["items"]}
    summaries = {}
    for fact in facts:
        summaries.update({i["item_id"]: (i, fact.fact_id) for i in fact.view.get("items", [])})
    eligible = [
        item
        for item in catalog
        if catalog[item]["relation_eligible"]
        and item in summaries
        and summaries[item][0].get("state") == "available"
        and summaries[item][0].get("has_variance")
        and len(summaries[item][0].get("exploration_thresholds", [])) >= 2
    ]
    if goal.items and goal.kind == "priority":
        eligible = list(dict.fromkeys([i for i in goal.items if i in eligible] + eligible))
    candidates = {}

    def add(name, args, basis, reason):
        try:
            executor.parse(name, args)
        except ValueError:
            return
        key = query_key(name, args)
        if len(candidates) < MAX_DIRECTIONS:
            candidates[key] = {
                "direction_id": "direction-" + sha256(key.encode()).hexdigest()[:16],
                "tool": name,
                "arguments": args,
                "basis_fact_ids": list(basis),
                "reason_code": reason,
            }

    def contrast(conditions, outcome, basis, reason):
        if all(i in eligible for i in (*conditions, outcome)):
            add(
                "compare_student_groups",
                {
                    "conditions": [
                        {
                            "item_id": i,
                            "operator": "<=",
                            "threshold": summaries[i][0]["exploration_thresholds"][0],
                        }
                        for i in conditions
                    ],
                    "outcomes": [outcome],
                },
                basis,
                reason,
            )

    if goal.kind in ("relation", "explanation"):
        if all(catalog[i]["relation_eligible"] for i in goal.items):
            add(
                "query_dimension_relation",
                dict(zip(("left", "right"), goal.items, strict=True)),
                (),
                "requested_pair",
            )
    elif goal.kind == "group":
        add(
            "query_dimension_relation",
            {"left": goal.items[0], "right": goal.items[-1]},
            (),
            "marginal_context_for_requested_contrast",
        )
        contrast(goal.items[:-1], goal.items[-1], (), "requested_combination")
    elif goal.kind == "quality":
        # Published summary already covers these counts; don't repackage it as new knowledge.
        covered = {
            i for i, (s, _) in summaries.items() if s.get("state") == "available" and "states" in s
        }
        for item in goal.items:
            if item not in covered:
                add("inspect_item_quality", {"item_id": item}, (), "unresolved_data_quality")
    else:
        issues = [
            (i, s, basis)
            for i, (s, basis) in summaries.items()
            if s.get("state") == "available"
            and any(n > 0 for state, n in s.get("states", {}).items() if state != "valid")
        ]
        if issues:
            item, _, basis = max(
                issues,
                key=lambda row: (
                    sum(n for state, n in row[1]["states"].items() if state != "valid"),
                    row[0],
                ),
            )
            add("inspect_item_quality", {"item_id": item}, (basis,), "observed_quality_issue")
        if len(eligible) >= 2:
            add(
                "query_dimension_relation",
                {"left": eligible[0], "right": eligible[1]},
                (summaries[eligible[0]][1],),
                "bounded_pair_exploration",
            )
        relations = [f for f in facts if f.view.get("type") == "item_relation"]
        for fact in relations[:1]:
            v = fact.view["metrics"]
            if v["rho"] is None:
                continue
            if abs(v["rho"]) >= executor.kernel.policy.correlation_threshold:
                contrast(
                    (v["left"],),
                    v["right"],
                    (fact.fact_id,),
                    "check_disjoint_groups_after_association",
                )
            elif len(eligible) >= 3:
                contrast(
                    eligible[:2],
                    eligible[2],
                    (fact.fact_id,),
                    "marginal_relation_does_not_test_joint_conditions",
                )
    executed = {query_key(f.tool, f.query["arguments"]): f.fact_id for f in facts}
    rejected = {
        query_key(a["tool"], a["arguments"]): a["error"]
        for a in executor.attempt_ledger
        if a["status"] == "rejected"
    }
    directions = []
    for key, candidate in candidates.items():
        candidate = dict(candidate)
        candidate["status"] = (
            "verified" if key in executed else "blocked" if key in rejected else "uninvestigated"
        )
        candidate["verified_fact_id"] = executed.get(key)
        candidate["error"] = rejected.get(key)
        directions.append(candidate)
    pending = [d for d in directions if d["status"] == "uninvestigated"]
    blocked = [d for d in directions if d["status"] == "blocked"]
    status = (
        "budget_limited"
        if pending and (tools_remaining <= 0 or models_remaining <= 0)
        else "incomplete"
        if pending
        else "evidence_limited"
        if blocked
        else "bounded_complete"
        if directions
        else "no_eligible_directions"
    )
    numeric = eligible
    investigated_pairs = {
        frozenset((f.view["metrics"]["left"], f.view["metrics"]["right"]))
        for f in facts
        if f.view.get("type") == "item_relation"
    }
    unexamined_pairs = [
        (a, b)
        for i, a in enumerate(numeric)
        for b in numeric[i + 1 :]
        if frozenset((a, b)) not in investigated_pairs
    ]
    return {
        "uninvestigated_pairs": {
            "count": len(unexamined_pairs),
            "examples": unexamined_pairs[:8],
            "examples_truncated": len(unexamined_pairs) > 8,
        },
        "global_investigation_exhaustive": False,
        "policy_version": VERSION,
        "scope": "bounded_data_derived_frontier",
        "status": status,
        "directions": directions,
        "uncovered_direction_ids": [d["direction_id"] for d in pending + blocked],
        "remaining_tool_attempts": max(0, tools_remaining),
        "remaining_model_requests": max(0, models_remaining),
        "can_continue": bool(pending and tools_remaining > 0 and models_remaining > 0),
        "all_pairs_or_thresholds_searched": False,
        "limitations": [
            "not_exhaustive",
            "candidate_does_not_predetermine_result",
            "privacy_and_qualification_checked_only_on_execution",
        ],
    }
