"""PR #47 CandidateRun factory injection: no import of unmerged code or oracle answers.

Requires the contracts module as an explicit dependency. Only registered built-in
synthetic recipes are accepted; unsupported cases are truthfully not_run.
Full evaluate integration awaits summary/support Claim and trace grading support.
"""

import asyncio
import cProfile
from pathlib import Path

from app.ai.edu.discovery import AdaptiveFake, run_agent
from app.ai.edu.discovery.synthetic import NAMES, rows
from app.analysis.edu.discovery import sources


def stats(value) -> dict:
    return {
        "valid": value.valid_count,
        "missing": value.missing_count,
        "invalid": value.invalid_count,
        "mean": value.mean,
        "median": value.median,
        "minimum": value.minimum,
        "maximum": value.maximum,
        "distribution": tuple(
            (f.score, f.share.count, f.share.denominator) for f in value.distribution
        ),
    }


def claim(fact, run, contracts):
    """Projection only. No averages, ranks, qualification or oracle values computed here."""
    discovery = fact.discovery
    p = discovery.payload

    def predicates(cs):
        return tuple(
            contracts.Predicate(c.dimension.name, c.operator.value, str(c.threshold)) for c in cs
        )

    if discovery.type == "dimension_relation":
        query = contracts.Query("relation", p.left.name, p.right.name)
        values = {
            "paired_count": p.paired_count,
            "excluded_count": len(p.excluded_records),
            "state": p.state.value,
            "rho": p.coefficient,
        }
    elif discovery.type == "group_difference":
        query = contracts.Query(
            "group", conditions=predicates(p.selection.conditions), outcome=p.outcome_dimension.name
        )
        sel = p.selection
        values = {
            "target_count": len(sel.matches),
            "remainder_count": len(p.remainder),
            "unevaluable_count": len(sel.unevaluable_records),
            "population_count": sel.scope.student_count,
            "eligible_count": sel.eligible_count,
            "share_total": sel.matched_share.proportion,
            "share_eligible": sel.matched_among_eligible.proportion,
        }
        for d in p.dimensions:
            values[d.dimension.name] = {
                "target": stats(d.target),
                "remainder": stats(d.remainder),
                "difference": d.difference,
                "condition_defined": d.condition_defined,
            }
    elif discovery.type == "rare_combination":
        query = contracts.Query("rare", p.left.name, p.right.name, side=p.side)
        values = {
            "pair_count": p.pair_count,
            "excluded_count": p.excluded_count,
            "q1": p.q1,
            "q3": p.q3,
            "median": p.median,
            "lower_fence": p.lower_fence,
            "upper_fence": p.upper_fence,
            "match_count": len(p.matches),
            "distribution": p.distribution,
            "pattern": p.pattern,
        }
    elif discovery.type == "boundary_sensitivity":
        varying = next(
            a.dimension.name
            for a, b in zip(p.strict.conditions, p.inclusive.conditions, strict=True)
            if a.operator != b.operator
        )
        query = contracts.Query(
            "boundary", left=varying, conditions=predicates(p.inclusive.conditions)
        )
        values = {
            "strict_count": len(p.strict.matches),
            "inclusive_count": len(p.inclusive.matches),
            "added_count": len(p.added),
            "removed_count": 0,
            "population_count": p.strict.scope.student_count,
            "eligible_count": p.strict.eligible_count,
            "unevaluable_count": len(p.strict.unevaluable_records),
            "observed_boundary": p.observed_boundary,
        }
    else:
        raise ValueError("unsupported_claim")
    bindings = {
        s.metric_key: tuple(
            contracts.Binding(
                (r.batch_id, r.student_id, r.record_id, r.source_id),
                tuple(
                    (e.source_id, e.location, e.kind.value, e.dimension.name if e.dimension else "")
                    for e in refs
                ),
            )
            for r, refs in sources(discovery, s.metric_key)
        )
        for s in discovery.sources
    }
    return contracts.Claim(
        fact.fact_id,
        run.run_id,
        discovery.context.batch_id,
        discovery.context.snapshot_id,
        discovery.rule_version,
        query,
        values,
        bindings,
    )


class AgentCandidate:
    def __init__(self, contracts, planner_factory=AdaptiveFake):
        self.contracts, self.planner_factory = contracts, planner_factory
        self.runs = {}  # Companion full trace; tool_trace is not the complete execution trace.

    def run(self, case, profile):
        if profile != "C_agent":
            raise ValueError("agent_profile_required")
        recipe = next((name for name in NAMES if rows(name) == case.rows), None)
        if recipe is None or case.distractors:
            return self.contracts.CandidateRun(
                "not-run",
                "not_run",
                reason="unregistered_synthetic_recipe",
                measurement_origin="offline_mechanism",
            )
        profiler = cProfile.Profile()
        # CLI adapter only. Async service code must await run_agent directly.
        with profiler:
            run = asyncio.run(
                run_agent(
                    recipe,
                    self.planner_factory(),
                    synthetic_context=(case.batch_id, case.snapshot_id, case.source_id),
                )
            )
        self.runs[run.run_id] = run
        ledger = tuple(claim(f, run, self.contracts) for f in run.facts if f.discovery is not None)
        names = {
            "discover",
            "compare_groups",
            "compare_boundary",
            "summarize",
            "dimension_stats",
            "select_students",
            "spearman_correlations",
            "_pair",
            "average_ranks",
        }
        api = Path(__file__).resolve().parents[2] / "apps/api"
        operations = {}
        for entry in profiler.getstats():
            code = entry.code
            if hasattr(code, "co_filename") and code.co_name in names:
                path = Path(code.co_filename).resolve()
                if path.is_relative_to(api):
                    operations[f"{path.relative_to(api).as_posix()}:{code.co_name}"] = (
                        entry.callcount
                    )
        return self.contracts.CandidateRun(
            run_id=run.run_id,
            status=run.reason if run.status == "completed" else "failed",
            reason=run.reason,
            ledger=ledger,
            selected_ids=run.proposed_ids,
            presented_ids=run.selected_ids,
            compute_seconds=run.tool_compute_seconds,
            engine_invocations=sum(n for key, n in operations.items() if key.endswith(":discover")),
            operation_counts=operations,
            engine_qualified_count=len(ledger),
            engine_returned_count=len(run.cards),
            search_attempts=sum(t.tool == "compare_student_groups" for t in run.tools),
            tool_trace=tuple(
                {
                    "tool": t.tool,
                    "arguments": t.arguments,
                    "status": t.status,
                    "fact_ids": list(t.fact_ids),
                    "error": t.error,
                }
                for t in run.tools
            ),
            input_tokens=None,
            output_tokens=None,
            cost=None,
            model_requests=run.model_requests,
            measurement_origin="offline_mechanism",
        )
