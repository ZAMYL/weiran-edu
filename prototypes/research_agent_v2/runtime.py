"""One existing session/executor and native Qwen dialogue; no parallel Agent engine."""

import json
from dataclasses import asdict, replace
from time import perf_counter
from uuid import uuid4

import httpx
from app.ai.edu.discovery.models import AgentError, Limits
from app.ai.edu.discovery.session import _run_session

from prototypes.adaptive_assessment.agent import AdaptiveExecutor, Decision, wire
from prototypes.adaptive_assessment.analysis import VERSION
from prototypes.adaptive_assessment.live.budget import CampaignBudget
from prototypes.adaptive_assessment.live.planner import QwenAdaptivePlanner
from prototypes.adaptive_assessment.synthetic import admit

from .goals import Goal, reflect, understand


class ResearchExecutor(AdaptiveExecutor):
    """Replay committed queries, including summaries/support, through the same kernel."""

    replay_executions = 0

    def _verify(self, fact):
        super()._verify(fact)
        replay = AdaptiveExecutor(self.source, self.run_id, self.limits)
        for original in self.facts.values():
            fresh = replay.execute(
                original.tool, replay.parse(original.tool, original.query["arguments"])
            )[0]
            self.replay_executions += 1
            if fresh != original:
                raise AgentError("source_validation_failed")
        if replay.released != self.released or replay.output_bytes != self.output_bytes:
            raise AgentError("disclosure_history_mismatch")


class ResearchPlanner(QwenAdaptivePlanner):
    def __init__(self, executor, budget, goal: Goal, **kwargs):
        self.goal, self.reflections, self.payloads = goal, [], []
        super().__init__(executor, budget, **kwargs)
        self.history[0]["content"] += (
            " Follow the controlled research_goal. Inspect server reflection before continuing. "
            "Goal satisfaction is checked by the server, never asserted by model prose."
        )

    async def _post(self, payload: dict) -> dict:
        # Base planner has already reconstructed/replayed all safe aggregates.
        payload = json.loads(json.dumps(payload))
        message = payload["messages"][-1]
        view = json.loads(message["content"])
        decision_context = reflect(self.goal, self._executor)
        view["research_goal"] = self.goal.view()
        view["reflection"] = decision_context
        self.reflections.append(decision_context)
        message["content"] = json.dumps(view, ensure_ascii=False)
        view_remaining = min(
            view["remaining_model_requests"], self._executor.limits.model_requests - self._requests
        )
        view["remaining_model_requests"] = max(0, view_remaining)
        message["content"] = json.dumps(view, ensure_ascii=False)
        if (
            len(json.dumps(payload, ensure_ascii=False).encode())
            > self._executor.limits.prompt_bytes
        ):
            raise AgentError("prompt_size_limit")
        self.payloads.append(json.loads(json.dumps(payload)))
        self.history = payload["messages"]
        return await super()._post(payload)


def fact_record(executor, fact) -> dict:
    executor._verify(fact)
    return {
        "fact_id": fact.fact_id,
        "batch_id": executor.kernel.data.batch.batch_id,
        "snapshot_id": fact.snapshot_id,
        "scope": "complete_registered_synthetic_batch",
        "query": fact.query,
        "values": fact.view,
        "rule_version": VERSION,
        "method": fact.tool,
        "source_replay_verified": True,
        "sources": [
            {
                "metric_key": s.key,
                "item_id": s.item_id,
                "record_count": len(s.references),
                "controlled_query": {"fact_id": fact.fact_id, "metric_key": s.key},
            }
            for s in fact.item_sources
        ],
    }


async def investigate(
    recipe: str,
    question: str,
    budget: CampaignBudget,
    *,
    transport: httpx.MockTransport | None = None,
    allow_network=False,
    limits: Limits | None = None,
    access="synthetic_full",
    seed_queries: tuple = (),
) -> tuple[dict, ResearchExecutor]:
    started = perf_counter()
    if access != "synthetic_full":
        raise AgentError("synthetic_access_denied")
    source = admit(recipe)
    source.validate()
    limits = limits or Limits(timeout_seconds=30)
    executor = ResearchExecutor(source, "research-" + uuid4().hex[:12], limits)
    goal = understand(question, executor.kernel.catalog())
    # Seed only recomputed current-run server queries, not #59 or imported historical Fact IDs.
    # Every attempt is charged; retained facts survive model selection and failures.
    if budget.snapshot()["claimed"]:
        raise AgentError("campaign_run_already_claimed")
    seed_ledger = []
    queries = seed_queries or (("get_dimension_statistics", {}),)
    if len(queries) >= limits.tool_attempts:
        raise AgentError("seed_budget_exhausted")
    for name, args in queries:
        facts = executor.execute(name, executor.parse(name, args))
        seed_ledger.append(
            {"phase": "trusted_seed", "tool": name, "fact_ids": [f.fact_id for f in facts]}
        )
    seeds = tuple(executor.facts)
    remaining = replace(limits, tool_attempts=limits.tool_attempts - len(queries))
    planner = ResearchPlanner(
        executor, budget, goal, transport=transport, allow_network=allow_network
    )
    try:
        run = await _run_session(
            executor,
            planner,
            limits=remaining,
            decision_type=Decision,
            argument_parser=executor.parse,
            analysis_context=executor.kernel.catalog(limits.minimum_cell),
        )
        # Preserve all verified initial observations independently of model's card selection.
        retained_cards = [i for i in seeds if executor.facts[i].view["qualified"]]
        all_selected = tuple(dict.fromkeys((*retained_cards, *run.selected_ids)))
        selected = all_selected[:3]
        cards = executor.finish(
            executor.parse(
                "finish",
                {
                    "selected_fact_ids": list(selected),
                    "reason": "completed" if selected else "no_valid_findings",
                },
            )
        )
        verdict = reflect(goal, executor)
        stop = (
            run.reason
            if run.status != "completed"
            else (
                verdict["state"]
                if verdict["state"] != "needs_investigation"
                else "insufficient_evidence"
                if run.reason == "insufficient_data"
                else "goal_unmet"
            )
        )
        verified = [fact_record(executor, f) for f in executor.facts.values()]
        return wire(
            {
                "origin": "offline_mechanism" if budget.origin == "http_mock" else "synthetic_live",
                "goal": goal.view(),
                "status": run.status,
                "stop_reason": stop,
                "observation_goal": verdict,
                "verified_observations": verified,
                "retained_fact_ids": list(seeds),
                "new_fact_ids": [f.fact_id for f in run.facts if f.fact_id not in seeds],
                "findings": cards,
                "display_limit": 3,
                "verified_but_not_displayed": [i for i in all_selected if i not in selected],
                "unsupported_explanations": [
                    "causal_teaching_effect",
                    "student_psychology",
                    "AI_dependency",
                ],
                "missing_evidence": verdict["evidence_gaps"],
                "next_check": (
                    "补充经审核的评分量规与原始任务；现有观察不证明原因。"
                    if goal.kind == "explanation"
                    else "按指标来源查询核对记录；群体差异不等于能力诊断。"
                ),
                "seed_ledger": seed_ledger,
                "tool_ledger": [asdict(t) for t in run.tools],
                "trace": [asdict(t) for t in run.trace],
                "reflections": planner.reflections,
                "native_requests": planner.payloads,
                "model_choices": planner.choices,
                "model_requests": run.model_requests,
                "tool_attempts": len(queries) + run.tool_attempts,
                "verification_replay_executions": executor.replay_executions,
                "seed_tool_attempts": len(queries),
                "elapsed_seconds": perf_counter() - started,
                "tool_compute_seconds": executor.compute_seconds,
                "budget": budget.snapshot(),
                "actual_tokens": None
                if budget.origin == "http_mock"
                else sum(m.total_tokens for m in planner.metrics)
                if planner.metrics
                and len(planner.metrics) == budget.snapshot()["request_count"]
                and all(m.total_tokens is not None for m in planner.metrics)
                else None,
                "actual_cost_cny": None,
                "estimated_list_price_cost_cny": None
                if budget.origin == "http_mock"
                else budget.snapshot()["charged_cny_upper_bound"],
                "real_qwen": "not_run"
                if budget.origin == "http_mock" or not budget.snapshot()["request_count"]
                else "attempted",
                "call_metrics": [asdict(m) for m in planner.metrics],
                "production_enabled": False,
                "formal_teacher_authorization": "not_implemented",
            }
        ), executor
    finally:
        await planner.aclose()


def markdown(result: dict) -> str:
    rows = [
        "# 合成教学数据调查",
        "",
        f"目标：{result['goal']['type']} {result['goal']['items']}",
        f"停止：{result['stop_reason']}",
        "",
        "## 已核验观察",
    ]
    for finding in result["findings"]:
        rows += [f"- {finding['title']}：{finding['summary']}"]
    if not result["findings"]:
        rows += ["- 本轮没有合格发现；不代表没有任何学习问题。"]
    rows += ["", "## 可复核统计概况", "|项目|有效/总数|均值|评分状态|", "|---|---|---|---|"]
    for fact in result["verified_observations"]:
        for item in fact["values"].get("items", []):
            value = item.get("mean") or {}
            rows.append(
                f"|{item['item_id']}|{item.get('valid_count', '未披露')}/"
                f"{item.get('denominator', '未披露')}|"
                f"{value.get('display', '不可计算')}|{item.get('states', item.get('state'))}|"
            )
    rows += [
        "",
        "## 下一步",
        result["next_check"],
        "",
        "相关不等于因果；离线Mock不证明真实模型的自主调查能力。",
        f"模型请求 {result['model_requests']}；工具尝试 {result['tool_attempts']}。",
        "实际Token和费用："
        + ("未发生真实模型调用" if result["real_qwen"] == "not_run" else "见预算账本"),
    ]
    return "\n".join(rows) + "\n"
