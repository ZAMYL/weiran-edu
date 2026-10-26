"""Offline decisions depend on computed tool results, never fixture names or answer labels."""

import json


class AdaptiveFake:
    offline_only = True

    async def decide(self, prompt: str) -> str:
        return json.dumps(self.choose(json.loads(prompt)))

    def choose(self, view: dict) -> dict:
        facts = view["facts"]
        if not facts:
            return {"name": "get_dimension_statistics", "arguments": {}}
        qualified = [f for f in facts if f["qualified"]]
        support = [f for f in facts if f.get("qualification") == "qualified"]
        if support:
            ids = [f["checked_fact_id"] for f in support]
            return {
                "name": "finish",
                "arguments": {"selected_fact_ids": ids[:3], "reason": "completed"},
                "basis_fact_ids": ids[:3],
            }
        groups = [f for f in qualified if f.get("card", {}).get("type") == "group_difference"]
        if groups:
            return {
                "name": "inspect_discovery_support",
                "arguments": {"fact_id": groups[0]["fact_id"]},
                "basis_fact_ids": [groups[0]["fact_id"]],
            }
        summaries = [f for f in facts if "dimensions" in f]
        summary = summaries[0]
        varying = [d for d in summary["dimensions"] if d.get("has_variance")]
        relations = [f for f in facts if "rho" in f]
        if (
            len(varying) >= 2
            and not relations
            and not view.get("last_result", {}).get("status") == "rejected"
        ):
            return {
                "name": "query_dimension_relation",
                "arguments": {"left": varying[0]["dimension"], "right": varying[1]["dimension"]},
                "basis_fact_ids": [summary["fact_id"]],
            }
        if (
            relations
            and varying[0].get("exploration_thresholds")
            and not any(f.get("differences") for f in facts)
            and abs(relations[0].get("rho") or 0) >= 0.6
            and view["last_result"]["status"] != "rejected"
        ):
            return {
                "name": "compare_student_groups",
                "arguments": {
                    "conditions": [
                        {
                            "dimension": varying[0]["dimension"],
                            "operator": "<=",
                            "threshold": varying[0]["exploration_thresholds"][0],
                        }
                    ],
                    "outcomes": [varying[1]["dimension"]],
                },
                "basis_fact_ids": [relations[0]["fact_id"]],
            }
        if qualified:
            chosen = qualified[0]["fact_id"]
            return {
                "name": "finish",
                "arguments": {"selected_fact_ids": [chosen], "reason": "completed"},
                "basis_fact_ids": [chosen],
            }
        return {
            "name": "finish",
            "arguments": {
                "selected_fact_ids": [],
                "reason": "insufficient_data"
                if summary["state"] == "insufficient_data"
                else "no_valid_findings",
            },
        }
