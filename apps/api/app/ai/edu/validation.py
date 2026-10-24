"""Validate IDs, then render trusted prose. The model cannot author factual sentences."""

import json

from pydantic import ValidationError

from .models import (
    EvidenceDocument,
    Explanation,
    Fact,
    InsightError,
    InsightReport,
    Observation,
    Proposal,
    RubricDocument,
    StrictModel,
)

HYPOTHESES = {
    "task_alignment": "待验证假设：测评任务与教学练习的要求可能存在差异，需结合任务及作品复核。",
    "practice_opportunity": "待验证假设：练习机会的差异可能影响当前表现，现有评分不能确认此解释。",
}
ACTIONS = {
    "guided_practice": "可考虑提供分步骤练习和反馈；先由教师核对观察依据及学生实际需要。",
    "review_rubric": "可考虑与评价者共同核对本批评分依据和任务要求；不得据此自动修改成绩。",
}
VERIFICATIONS = {
    "collect_work_samples": "在获得授权后检查对应作业与解题过程，并寻找支持及反驳该假设的证据。",
    "repeat_comparable_assessment": (
        "在确认任务和量表可比后再测并复核变化；变化本身不能证明教学因果效果。"
    ),
}
LIMITATIONS = {
    "correlation_is_not_causation": "相关关系不代表因果关系；本流程未提供因果证据。",
    "hypotheses_unconfirmed": "解释是假设，未经教师或新增证据确认。",
    "no_teaching_effect_verified": "本流程未实施或验证教学措施，不能声称已改善教学效果。",
    "templates_need_teacher_review": "引用有效不等于解释合理；模板选择的教学适切性仍需人工复核。",
    "offline_synthetic_not_auth": "当前仅限合成数据离线运行，不代表真实教师鉴权或外部模型授权。",
    "rubric_not_retrieved": "本次未取得可用评价依据，尚不能据此判断量表含义。",
    "evidence_not_retrieved": "本次未读取个人评分或评语证据，尚不能判断个体原因或评价一致性。",
    "missing_or_invalid_scores": "存在缺失、无效或不可判定数据，不能按零分解释。",
    "source_content_not_verified": (
        "统计基于调用方提供的规范化快照；本模块不验证源文件或评价依据真实性。"
    ),
    "no_personal_text_interpretation": (
        "证据只核对授权、绑定及可读取性；本流程未解释评语或量表正文语义。"
    ),
    "insufficient_data": "没有足够的有效分析数据，尚不能给出教学解释或建议。",
}


def parse_json(model: type[StrictModel], raw: str, max_bytes: int) -> StrictModel:
    if not isinstance(raw, str):
        raise InsightError("invalid_model_output")
    if len(raw.encode("utf-8")) > max_bytes:
        raise InsightError("model_output_limit")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InsightError("duplicate_json_key")
            result[key] = value
        return result

    def reject_constant(_):
        raise InsightError("invalid_json_constant")

    try:
        # Pydantic alone accepts duplicate JSON keys; reject before schema validation.
        json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
        return model.model_validate_json(raw)
    except (ValidationError, ValueError, RecursionError) as exc:
        if isinstance(exc, InsightError):
            raise
        raise InsightError("invalid_model_output") from None


def observation(fact: Fact) -> Observation:
    dimension = fact.dimension.value if fact.dimension else "条件组合"
    value = str(fact.value) if fact.value is not None else "不可计算"
    query = " AND ".join(
        f"{item.dimension.value}{item.operator.value}{item.threshold}" for item in fact.conditions
    )
    text = (
        f"{dimension}：{fact.metric}={value}；计算分母/样本数={fact.denominator}；"
        f"授权群体记录数={fact.scope.student_count}；"
    )
    if fact.dimension is None:
        text += f"查询维度不可判定={fact.unevaluable_count}。"
    else:
        text += f"缺失={fact.missing_count}，无效={fact.invalid_count}。"
    if query:
        text += f"条件：{query}。"
    return Observation(fact.fact_id, text)


def required_limitations(facts: tuple[Fact, ...], has_evidence: bool, has_rubric: bool) -> set[str]:
    codes = {
        "correlation_is_not_causation",
        "hypotheses_unconfirmed",
        "no_teaching_effect_verified",
        "templates_need_teacher_review",
        "offline_synthetic_not_auth",
        "source_content_not_verified",
        "no_personal_text_interpretation",
    }
    if not has_evidence:
        codes.add("evidence_not_retrieved")
    if not has_rubric:
        codes.add("rubric_not_retrieved")
    if any(item.missing_count or item.invalid_count or item.unevaluable_count for item in facts):
        codes.add("missing_or_invalid_scores")
    return codes


def evidence_matches_fact(document: EvidenceDocument | RubricDocument, fact: Fact) -> bool:
    """The same PR #5 integrity guard serves validation and model citation hints."""
    grant = document.grant
    dimensions = (
        {fact.dimension}
        if fact.dimension
        else {condition.dimension for condition in fact.conditions}
    )
    if fact.scope.batch_id != fact.batch_id:
        return False
    if isinstance(document, EvidenceDocument):
        if (
            grant.record.batch_id != fact.batch_id
            or grant.record not in fact.scope.records
            or grant.record not in fact.source_refs
        ):
            return False
    elif grant.batch_id != fact.batch_id:
        return False
    return grant.reference.dimension is None or grant.reference.dimension in dimensions


def validate_proposal(
    proposal: Proposal,
    facts: dict[str, Fact],
    evidence_ids: set[str],
    rubric_ids: set[str],
    documents: dict[str, EvidenceDocument | RubricDocument],
) -> InsightReport:
    def unique(values):
        if len(values) != len(set(values)):
            raise InsightError("duplicate_citation")
        return set(values)

    observed = unique(tuple(item.fact_id for item in proposal.observed_facts))
    cited_facts = unique(proposal.cited_fact_ids)
    cited_evidence = unique(proposal.cited_evidence_ids)
    if not observed <= facts.keys() or not cited_facts <= facts.keys():
        raise InsightError("unknown_fact_reference")
    if not cited_evidence <= evidence_ids:
        raise InsightError("unknown_evidence_reference")
    used_facts = set(observed)
    used_evidence: set[str] = set()
    for item in (*proposal.hypotheses, *proposal.teaching_actions, *proposal.verification_steps):
        references = unique(item.fact_ids)
        evidence = unique(item.evidence_ids)
        if not references <= observed:
            raise InsightError("unobserved_fact_basis")
        if not evidence <= evidence_ids:
            raise InsightError("unknown_evidence_reference")
        supported = [
            facts[identifier]
            for identifier in references
            if facts[identifier].value is not None
            and facts[identifier].denominator > 0
            and facts[identifier].metric
            in {"mean", "median", "minimum", "maximum", "matched_ratio", "matched_ratio_eligible"}
        ]
        if not supported:
            raise InsightError("unsupported_fact_basis")
        for identifier in evidence:
            document = documents[identifier]
            for fact_id in references:
                fact = facts[fact_id]
                if not evidence_matches_fact(document, fact):
                    raise InsightError("evidence_fact_scope_mismatch")
        used_facts.update(references)
        used_evidence.update(evidence)
    if cited_facts != used_facts or cited_evidence != used_evidence:
        raise InsightError("citation_union_mismatch")

    def explain(items, templates, status):
        return tuple(
            Explanation(item.kind, templates[item.kind], item.fact_ids, item.evidence_ids, status)
            for item in items
        )

    codes = required_limitations(
        tuple(facts.values()), bool(evidence_ids - rubric_ids), bool(rubric_ids)
    )
    codes.update(proposal.limitations)
    return InsightReport(
        tuple(observation(facts[item.fact_id]) for item in proposal.observed_facts),
        explain(proposal.hypotheses, HYPOTHESES, "hypothesis_unverified"),
        explain(proposal.teaching_actions, ACTIONS, "action_proposal"),
        explain(proposal.verification_steps, VERIFICATIONS, "verification_proposal"),
        tuple(LIMITATIONS[code] for code in sorted(codes)),
        proposal.cited_fact_ids,
        proposal.cited_evidence_ids,
    )


def insufficient_report(
    facts: dict[str, Fact], *, has_evidence: bool = False, has_rubric: bool = False
) -> InsightReport:
    codes = required_limitations(tuple(facts.values()), has_evidence, has_rubric)
    codes.add("insufficient_data")
    return InsightReport(
        tuple(observation(item) for item in facts.values()),
        (),
        (),
        (),
        tuple(LIMITATIONS[code] for code in sorted(codes)),
        tuple(facts),
        (),
    )
