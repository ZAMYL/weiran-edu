"""One existing session/executor and native Qwen dialogue; no parallel Agent engine."""

import json
from dataclasses import asdict, replace
from decimal import Decimal
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

from .completeness import assess
from .goals import Goal, reflect, understand


def remaining_requests(budget: CampaignBudget, limit: int, *, reserve_current=False) -> int:
    """Read-only conservative capacity, using the authoritative shared price and journal."""
    snapshot = budget.snapshot()
    if snapshot["blocked"]:
        return 0
    authorization = budget.authorization
    tokens = budget.price.max_input_tokens + authorization.max_output_tokens
    cost = budget.price.cost(budget.price.max_input_tokens, authorization.max_output_tokens)
    pending = int(reserve_current)
    return max(
        0,
        min(
            limit,
            authorization.max_requests - snapshot["request_count"] - pending,
            (authorization.total_token_limit - snapshot["charged_tokens"]) // tokens - pending,
            int((authorization.cost_cap_cny - Decimal(snapshot["charged_cny_upper_bound"])) / cost)
            - pending,
        ),
    )


class ResearchExecutor(AdaptiveExecutor):
    """Replay committed queries, including summaries/support, through the same kernel."""

    replay_executions = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attempt_ledger = []

    def execute(self, name, args):
        entry = {"tool": name, "arguments": args.model_dump(), "status": "attempted"}
        self.attempt_ledger.append(entry)
        try:
            facts = super().execute(name, args)
            entry.update(status="verified", fact_ids=[f.fact_id for f in facts])
            return facts
        except ValueError as error:
            entry.update(status="rejected", error=str(error))
            raise

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
        if set(payload) != {"messages", "tools", "tool_choice", "parallel_tool_calls"}:
            raise AgentError("unsafe_request_profile")
        if payload["tool_choice"] != "required" or payload["parallel_tool_calls"] is not False:
            raise AgentError("unsafe_request_profile")
        # Base planner has already reconstructed/replayed all safe aggregates.
        payload = json.loads(json.dumps(payload))
        message = payload["messages"][-1]
        view = json.loads(message["content"])
        decision_context = reflect(self.goal, self._executor)
        view["research_goal"] = self.goal.view()
        decision_context["coverage"] = assess(
            self.goal,
            self._executor,
            tools_remaining=view["remaining_tool_attempts"],
            models_remaining=remaining_requests(
                self._budget,
                self._executor.limits.model_requests - self._requests,
                reserve_current=True,
            ),
        )
        view["reflection"] = decision_context
        self.reflections.append(decision_context)
        message["content"] = json.dumps(view, ensure_ascii=False)
        view_remaining = min(
            view["remaining_model_requests"],
            decision_context["coverage"]["remaining_model_requests"],
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
        all_selected = tuple(
            dict.fromkeys(
                (
                    *retained_cards,
                    *run.selected_ids,
                    *(i for i, f in executor.facts.items() if f.view["qualified"]),
                )
            )
        )
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
        coverage = assess(
            goal,
            executor,
            tools_remaining=limits.tool_attempts - len(queries) - run.tool_attempts,
            models_remaining=remaining_requests(budget, limits.model_requests - run.model_requests),
        )
        if run.status == "completed" and stop != "needs_external_evidence":
            if coverage["status"] in {"budget_limited", "evidence_limited"}:
                stop = coverage["status"]
            elif coverage["uncovered_direction_ids"]:
                stop = "stopped_with_uncovered_directions"
            elif goal.kind == "priority" and coverage["uninvestigated_pairs"]["count"]:
                stop = "bounded_scope_stop"
        verified = [fact_record(executor, f) for f in executor.facts.values()]
        indexed = {f["fact_id"]: f for f in verified}
        tool_events = [
            e for e in run.trace if e.phase == "tool_execution" and e.status == "attempted"
        ]
        stages = [
            {
                "phase": "initial_overview",
                "tool": row["tool"],
                "facts": [indexed[i] for i in row["fact_ids"]],
                "tool_attempts_used": i + 1,
                "model_requests_used": 0,
            }
            for i, row in enumerate(seed_ledger)
        ]
        stages += [
            {
                "phase": "investigation",
                "tool": t.tool,
                "status": t.status,
                "arguments": t.arguments,
                "error": t.error,
                "facts": [indexed[i] for i in t.fact_ids],
                "tool_attempts_used": len(queries) + t.attempt,
                "model_requests_used": tool_events[i].round,
            }
            for i, t in enumerate(run.tools)
        ]
        return wire(
            {
                "origin": "offline_mechanism" if budget.origin == "http_mock" else "synthetic_live",
                "goal": goal.view(),
                "status": run.status,
                "stop_reason": stop,
                "observation_goal": verdict,
                "investigation_coverage": coverage,
                "investigation_status": "partial"
                if coverage["uncovered_direction_ids"]
                else "bounded_complete_not_exhaustive",
                "attempt_ledger": executor.attempt_ledger,
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
                "stages": stages,
                "model_selected_fact_ids": run.selected_ids,
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
    names = {
        "priority": "优先核查评分模式",
        "group": "核对指定能力组合的评分差异",
        "relation": "调查指定评价项的统计关联",
        "quality": "核查缺分和有效样本",
        "explanation": "核查关联背后是否有足够教学依据",
    }
    coverage = result["investigation_coverage"]
    stop_labels = {
        "goal_satisfied": "明确问题已有对应统计证据",
        "bounded_scope_stop": "完成本轮有限核查，未进行穷尽调查",
        "stopped_with_uncovered_directions": "规划器主动结束，仍有未核查方向",
        "budget_limited": "预算不足，保留结果并停止",
        "evidence_limited": "资料或披露边界阻止继续",
        "needs_external_evidence": "缺少量规或任务依据，不能解释原因",
        "goal_unmet": "尚未满足调查目标",
        "no_supported_findings": "本轮未发现合格评分模式",
        "model_timeout": "请求超时，调查未完成",
        "model_budget_exhausted": "请求预算用尽，调查未完成",
    }
    rows = [
        "# 未然 EDU｜合成测评调查",
        "",
        "教师问题（受控重述）："
        + names[result["goal"]["type"]]
        + "："
        + "、".join(result["goal"]["items"]),
        "",
        "## 已证实的观察",
    ]
    for fact in result["verified_observations"]:
        if fact["values"].get("type") == "item_relation" and not fact["values"]["qualified"]:
            v = fact["values"]["metrics"]
            rows += [
                f"- 前置核查：{v['left']}与{v['right']}，有效配对{v['paired_count']}人，"
                f"Spearman={v['rho']}；不可据此排除条件组合中的差异。"
            ]
    for card in result["findings"]:
        rows += ["- " + card["title"] + "：" + card["summary"]]
        v = card["metrics"]
        if card["type"] == "item_group_difference":
            rows += [
                "  - 实际筛选条件："
                + " 且 ".join(
                    f"{p['item_id']} {p['operator']} {p['threshold']}" for p in v["conditions"]
                ),
                f"  - 目标{v['target_count']}人，其余{v['remainder_count']}人；"
                f"总分母{v['denominator']}人，"
                f"条件可判定{v['eligible_denominator']}人；目标/其余组有效评分"
                f"{v['target_valid']}/{v['remainder_valid']}条；均值差{v['difference']['display']}。",
            ]
    if not result["findings"]:
        rows += ["- 当前没有合格发现；不能据此判断没有学习问题。"]
    if any(
        f["values"].get("type") == "item_relation" and f["values"]["metrics"]["rho"] == 0
        for f in result["verified_observations"]
    ) and any(c["type"] == "item_group_difference" for c in result["findings"]):
        rows += [
            "",
            "为什么继续核查：整体等级相关为0不能排除条件组合内的群体差异。"
            "本轮进一步比较了实际筛选群体；这仍是描述性观察，不是因果证明。",
        ]
    rows += [
        "",
        "## 调查边界",
        "目标状态："
        + (
            "已有对应统计观察；这不等于调查充分。"
            if result["observation_goal"]["matching_fact_ids"]
            else "本轮尚无对应的合格观察，或评分模式不支持此项发现。"
        ),
        "本轮只覆盖声明的有限候选范围，不是全体项目/阈值的穷尽调查。",
        f"前沿未覆盖{len(coverage['uncovered_direction_ids'])}个方向；"
        f"另有{coverage['uninvestigated_pairs']['count']}对可分析项目尚未检验。",
        "停止原因：" + stop_labels.get(result["stop_reason"], "安全停止，见技术记录") + "。",
        "",
        "## 下一步",
        result["next_check"],
        "不得将相关或评分差异解释为教学因果、能力等级或学生心理。",
        "",
        "本记录为合成数据与HTTP Mock机制演示，不是实际Qwen推理效果。",
        f"本轮模型协议请求{result['model_requests']}次，工具尝试{result['tool_attempts']}次；"
        f"另有{result['verification_replay_executions']}次校验重放。实际费用未发生。",
    ]
    return "\n".join(rows) + "\n"


def technical_markdown(result: dict) -> str:
    sections = ["# Research Agent V2.1 技术执行记录", "", "仅合成HTTP Mock；非真实模型效果。"]
    for key in (
        "goal",
        "stages",
        "reflections",
        "model_choices",
        "model_selected_fact_ids",
        "verified_observations",
        "investigation_coverage",
        "trace",
        "budget",
        "native_requests",
    ):
        sections += [
            "",
            "## " + key,
            "```json",
            json.dumps(result[key], ensure_ascii=False, indent=2),
            "```",
        ]
    return "\n".join(sections) + "\n"
