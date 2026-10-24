"""Small internal presentation projection, not a public API or frontend contract."""

from dataclasses import dataclass

from .models import Outcome


@dataclass(frozen=True)
class InsightCard:
    title: str
    status: str
    execution_mode: str
    observations: tuple[dict[str, object], ...]
    hypotheses: tuple[str, ...]
    further_exploration: tuple[str, ...]
    cited_fact_ids: tuple[str, ...]
    cited_evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    error_code: str | None


def project_card(outcome: Outcome) -> InsightCard:
    if outcome.report is None:
        return InsightCard(
            "学情分析暂未完成",
            "error",
            outcome.execution_mode,
            (),
            (),
            (),
            (),
            (),
            (),
            outcome.error_code,
        )
    facts = {fact.fact_id: fact for fact in outcome.facts}
    observations = []
    for observation in outcome.report.observed_facts[:6]:
        fact = facts[observation.fact_id]
        observations.append(
            {
                "fact_id": fact.fact_id,
                "batch_id": fact.batch_id,
                "dimension": fact.dimension.value if fact.dimension else None,
                "metric": fact.metric,
                "value_exact": str(fact.value) if fact.value is not None else None,
                "denominator": fact.denominator,
                "scope_count": fact.scope.student_count,
                "conditions": fact.model_view()["conditions"],
                "text": observation.text,
                "rule_id": fact.rule_id,
                "rule_version": fact.rule_version,
                "missing_count": fact.missing_count,
                "invalid_count": fact.invalid_count,
                "unevaluable_count": fact.unevaluable_count,
            }
        )
    filtered = any(item.conditions for item in facts.values())
    matched_counts = [item for item in facts.values() if item.metric == "matched_count"]
    no_matches = bool(matched_counts) and all(item.value == 0 for item in matched_counts)
    status = (
        "insufficient_data"
        if outcome.status == "insufficient_data"
        else "no_matches"
        if no_matches
        else "needs_teacher_verification"
    )
    return InsightCard(
        "群体筛选学情发现" if filtered else "五维学情概况",
        status,
        outcome.execution_mode,
        tuple(observations),
        tuple(item.text for item in outcome.report.hypotheses),
        tuple(item.text for item in outcome.report.verification_steps),
        outcome.report.cited_fact_ids,
        outcome.report.cited_evidence_ids,
        outcome.report.limitations,
        None,
    )
