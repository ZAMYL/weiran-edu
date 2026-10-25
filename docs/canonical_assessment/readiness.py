"""Deterministic readiness only: no statistics engine, database, files or network."""

from dataclasses import asdict, dataclass
from itertools import combinations

from .model import PROFILE, AssessmentItem, Dataset, Observation, Purpose, exact


@dataclass(frozen=True)
class AnalysisCapability:
    analysis: str
    item_ids: tuple[str, ...]
    eligible_count: int
    denominator: int
    reason: str


@dataclass(frozen=True)
class ResourceSuggestion:
    missing: str
    why: str
    enables: str
    can_continue: bool
    requirement: str
    scope: str


@dataclass(frozen=True)
class AnalysisReadiness:
    dataset_id: str
    dataset_version: str
    batch_id: str
    batch_version: str
    rule_version: str
    purpose: Purpose
    recognized_items: tuple[dict, ...]
    available_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    quality_issues: tuple[dict, ...]
    available_analyses: tuple[AnalysisCapability, ...]
    partially_available_analyses: tuple[AnalysisCapability, ...]
    unavailable_analyses: tuple[AnalysisCapability, ...]
    suggested_resources: tuple[ResourceSuggestion, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def score_state(observation: Observation | None, item: AssessmentItem) -> str:
    if observation is None or observation.raw_decimal is None:
        return "missing"
    try:
        value = exact(observation.raw_decimal)
    except ValueError:
        return "invalid_numeric"
    if item.scale is None:
        return "range_unknown"
    scale = item.scale
    if not exact(scale.minimum) <= value <= exact(scale.maximum):
        return "out_of_range"
    if ((value - exact(scale.minimum)) / exact(scale.step)).denominator != 1:
        return "off_step"
    return "valid"


def assess(dataset: Dataset, purpose: Purpose = "teacher_analysis") -> AnalysisReadiness:
    dataset.validate()
    if purpose not in ("teacher_analysis", "display", "external_model"):
        raise ValueError("unknown_purpose")
    refs = {e.evidence_id: e for e in dataset.evidence}
    observations = {(o.participant_id, o.item_id): o for o in dataset.observations}
    n = len(dataset.participants)
    fields, missing, issues, suggestions, recognized = set(), set(), [], [], []
    eligible: dict[str, set[str]] = {}
    reviewable: dict[str, set[str]] = {}
    groups: dict[str, list[AnalysisCapability]] = {
        "available": [],
        "partial": [],
        "unavailable": [],
    }

    def availability(evidence_ids: tuple[str, ...], kind: str) -> str:
        matching = [refs[e] for e in evidence_ids if refs[e].kind == kind]
        if not matching:
            return "absent"
        trusted = [e for e in matching if e.provenance in ("user_confirmed", "retrieved")]
        if not trusted:
            return "unconfirmed_source"
        return "available" if any(purpose in e.permitted_purposes for e in trusted) else "withheld"

    def allowed(evidence_ids: tuple[str, ...], kind: str) -> bool:
        return availability(evidence_ids, kind) == "available"

    def capability(name: str, item_ids: tuple[str, ...], count: int, reason: str) -> None:
        state = "unavailable" if count == 0 else "available" if count == n else "partial"
        groups[state].append(AnalysisCapability(name, item_ids, count, n, reason))

    for item in dataset.items:
        eligible[item.item_id], reviewable[item.item_id] = set(), set()
        counts: dict[str, int] = {}
        evidence_counts: dict[str, dict[str, int]] = {
            k: {} for k in ("score", "comment", "response")
        }
        rubric = next((r for r in dataset.rubrics if r.rubric_id == item.rubric_id), None)
        criteria_ok = rubric is not None and allowed((rubric.criteria_evidence_id,), "criteria")
        if criteria_ok:
            fields.add(f"{item.item_id}:authorized_criteria")
        for participant in dataset.participants:
            key = (participant.participant_id, item.item_id)
            observation = observations.get(key)
            for kind in ("score", "comment", "response"):
                field = f"{item.item_id}:{kind}"
                status = availability(observation.evidence_ids, kind) if observation else "absent"
                evidence_counts[kind][status] = evidence_counts[kind].get(status, 0) + 1
                if status == "available":
                    fields.add(field)
                else:
                    missing.add(field)
            if observation and not allowed(observation.evidence_ids, "score"):
                counts["unavailable_score"] = counts.get("unavailable_score", 0) + 1
                issues.append(
                    {
                        "item_id": item.item_id,
                        "participant_id": key[0],
                        "code": "score_evidence_" + availability(observation.evidence_ids, "score"),
                    }
                )
                continue
            state = score_state(observation, item)
            counts[state] = counts.get(state, 0) + 1
            if state != "valid":
                issues.append({"item_id": item.item_id, "participant_id": key[0], "code": state})
            if state not in ("valid", "range_unknown") or observation is None:
                continue
            eligible[item.item_id].add(key[0])
            if (
                state == "valid"
                and criteria_ok
                and all(allowed(observation.evidence_ids, kind) for kind in ("comment", "response"))
            ):
                reviewable[item.item_id].add(key[0])
        recognized.append(
            {
                "item_id": item.item_id,
                "name": item.name,
                "kind": item.kind,
                "scale": asdict(item.scale) if item.scale else None,
                "confirmed_profile_code": item.confirmed_profile_code,
                "quality_counts": counts,
                "evidence_availability": evidence_counts,
                "criteria_availability": (
                    availability((rubric.criteria_evidence_id,), "criteria") if rubric else "absent"
                ),
            }
        )
        valid_count = len(eligible[item.item_id])
        capability(
            "item_statistics",
            (item.item_id,),
            valid_count,
            "按原量表的授权数值分析；未知量表仅描述原始数值，不解释等级。",
        )
        capability(
            "evaluation_review",
            (item.item_id,),
            len(reviewable[item.item_id]),
            "需同记录评分、评语、答卷及适用标准；准备就绪仅允许教师复核，不判定对错。",
        )
        if not criteria_ok:
            missing.add(f"{item.item_id}:authorized_criteria")
            suggestions.append(
                ResourceSuggestion(
                    f"{item.item_id} 的可用评价标准",
                    "缺失、未授权或来源未确认，无法解释评分依据。",
                    "基于标准的人工评价复核",
                    True,
                    "optional",
                    item.item_id,
                )
            )
        for kind, label in (("comment", "评语"), ("response", "原始答卷")):
            for status, count in evidence_counts[kind].items():
                if status == "available":
                    continue
                action = {
                    "absent": f"补充 {count} 条对应记录的{label}",
                    "withheld": f"申请 {count} 条已有{label}的 {purpose} 用途授权",
                    "unconfirmed_source": f"确认 {count} 条已有{label}的来源",
                }[status]
                suggestions.append(
                    ResourceSuggestion(
                        f"{item.item_id}：{action}",
                        "仅有分数不能核查答题过程；不编造资料，也不把扣留当作没有。",
                        "评分、标准及其他必要资料也可用时进行更深入的教师复核",
                        True,
                        "optional",
                        item.item_id,
                    )
                )
        if item.scale is None:
            missing.add(f"{item.item_id}:scale")
            suggestions.append(
                ResourceSuggestion(
                    f"{item.item_id} 的原量表范围、单位和步长",
                    "当前无法核验评分范围及等级含义。",
                    "范围/精度校验和量表内解释",
                    True,
                    "optional",
                    item.item_id,
                )
            )
        for state, count in counts.items():
            if state not in ("valid", "range_unknown", "unavailable_score") and count:
                suggestions.append(
                    ResourceSuggestion(
                        f"{item.item_id} 的 {count} 条 {state} 评分",
                        "不能把缺分或非法评分当作零分或完整数据。",
                        "恢复受影响记录的统计资格",
                        valid_count > 0,
                        "quality_blocker",
                        "affected_observations",
                    )
                )
        if counts.get("unavailable_score"):
            suggestions.append(
                ResourceSuggestion(
                    f"{item.item_id} 的可信评分来源及 {purpose} 用途授权",
                    "缺少评分证据、来源未确认或已有评分未授权使用，当前不读取其数值。",
                    "恢复获确认和授权的评分分析",
                    valid_count > 0,
                    "authorization_or_source_blocker",
                    "affected_observations",
                )
            )
    for left, right in combinations(dataset.items, 2):
        ids = (left.item_id, right.item_id)
        count = len(eligible[ids[0]] & eligible[ids[1]])
        capability(
            "item_combination_exploration",
            ids,
            count,
            "仅同一参与者的原始分值联合分布；不求跨量表总分、相关显著性或因果结论。",
        )
    covered = {i.confirmed_profile_code for i in dataset.items}
    absent = tuple(code for code in PROFILE if code not in covered)
    if absent:
        suggestions.append(
            ResourceSuggestion(
                "推荐 Profile 未确认覆盖：" + "、".join(PROFILE[code] for code in absent),
                "仅相对默认推荐方案的覆盖差异；自定义名称不能由 AI 猜测映射。",
                "教师确认映射或另行采集后，可扩展相应维度的分析",
                True,
                "optional",
                "profile",
            )
        )
    groups["unavailable"].append(
        AnalysisCapability(
            "longitudinal_progress",
            (),
            0,
            n,
            "多次测评本身不证明可比；本原型不实现跨批次比较或能力进步判定。",
        )
    )
    if dataset.history_batch_ids:
        suggestions.append(
            ResourceSuggestion(
                "跨批次量表/任务难度、时间、参与者匹配及可比性确认依据",
                "同名项目与重复测评不足以证明能力进步。",
                "未来可比范围内的变化描述；仍不证明因果",
                True,
                "optional",
                "longitudinal_progress",
            )
        )
    return AnalysisReadiness(
        dataset.dataset_id,
        dataset.version,
        dataset.batch.batch_id,
        dataset.batch.version,
        "canonical.readiness.synthetic.v1",
        purpose,
        tuple(recognized),
        tuple(sorted(fields)),
        tuple(sorted(missing)),
        tuple(issues),
        tuple(groups["available"]),
        tuple(groups["partial"]),
        tuple(groups["unavailable"]),
        tuple(suggestions),
        (
            "完全合成的离线设计验证；能力状态不表示分析已执行。",
            "缺失、非法、未授权记录均不补造；有效子集的数量与全体分母分别报告。",
            "本原型的授权是夹具声明，不是真实服务端鉴权；不接数据库或任何模型。",
            "不会自动跨量表比较、把题目映射为能力或判断能力进步。",
        ),
    )
