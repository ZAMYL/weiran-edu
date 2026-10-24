from collections import defaultdict
from collections.abc import Iterable
from fractions import Fraction

from ..engine import cohort
from ..models import (
    AnalysisInputError,
    Dataset,
    Dimension,
    EvidenceKind,
    EvidenceRef,
    RecordRef,
    ScorePolicy,
    ScoreState,
    ValidatedRecord,
)
from .classify import classify_comment
from .models import (
    Classification,
    ClassificationState,
    CommentInput,
    CommentRef,
    DuplicateGroup,
    Finding,
    FindingKind,
    NumericClaim,
    RecordReview,
    ReviewReport,
    ReviewState,
)
from .rules import DEFAULT_RULES, RuleSet

REASONS = {
    "empty_comment": "评语为空，无法据此判断评价一致性。",
    "unclassified_comment": "当前草案词典无法分类，需要人工阅读。",
    "guarded_context": "包含否定、举例、引用、历史、疑问或条件语境，规则停止自动冲突判断。",
    "no_supported_complete_numeric_assertion": "未获完整评分断言，描述性文字需人工判断。",
    "multiple_different_stated_scores": "同一评语对同一维度出现多个不同分数，需要人工确认所指。",
    "comment_dimension_mismatch": "评语来源所关联维度与评分断言不同，需要人工核对。",
    "recorded_score_missing_or_invalid": "原评分缺失或无效，不能进行可靠数值对照。",
    "stated_score_outside_policy": "文本分数不符合当前评分范围或步长，需要人工复核。",
    "score_evidence_missing": "未提供相应评分依据引用，不能确认数值冲突。",
    "explicit_stated_score_differs_from_record": "明确文本分数与原评分不同，仅提示复核。",
    "repeated_text_is_not_itself_an_error": "文本重复，供检查，不据此判错。",
    "no_comment_supplied": "当前群体内该记录未提供评语，无法据此判断一致性。",
}


def _finding(
    rules: RuleSet,
    rule_id: str,
    kind: FindingKind,
    reason: str,
    assessments: tuple[RecordRef, ...],
    comments: tuple[CommentRef, ...],
    claims: tuple[NumericClaim, ...] = (),
    score: Fraction | None = None,
    score_evidence: tuple[EvidenceRef, ...] = (),
) -> Finding:
    return Finding(
        rule_id,
        rules.version,
        rules.fingerprint,
        kind,
        reason,
        REASONS[reason],
        assessments,
        comments,
        tuple(claim.hit for claim in claims),
        claims[0].dimension if claims else None,
        score,
        claims[0].value if len({claim.value for claim in claims}) == 1 else None,
        score_evidence,
    )


def _score_findings(
    classification: Classification,
    record: ValidatedRecord,
    policy: ScorePolicy,
    rules: RuleSet,
) -> tuple[Finding, ...]:
    assessments, comments = (record.reference,), (classification.comment,)
    if not classification.numeric_context_clear:
        reason = (
            "empty_comment"
            if classification.state is ClassificationState.EMPTY
            else "unclassified_comment"
            if classification.state is ClassificationState.UNCLASSIFIED
            else "guarded_context"
            if classification.guard_hits
            else "no_supported_complete_numeric_assertion"
        )
        return (
            _finding(
                rules,
                "review.manual_context",
                FindingKind.MANUAL,
                reason,
                assessments,
                comments,
                classification.claims,
            ),
        )
    by_dimension: dict[Dimension, list[NumericClaim]] = defaultdict(list)
    for claim in classification.claims:
        by_dimension[claim.dimension].append(claim)
    findings = []
    for dimension, values in by_dimension.items():
        claims = tuple(values)
        score = record.score(dimension)
        evidence = tuple(
            ref
            for ref in record.reference.evidence_refs
            if ref.kind is EvidenceKind.SCORE and ref.dimension in {None, dimension}
        )
        text_value = claims[0].value
        reason = None
        if len({claim.value for claim in claims}) != 1:
            reason = "multiple_different_stated_scores"
        elif classification.comment.evidence.dimension not in {None, dimension}:
            reason = "comment_dimension_mismatch"
        elif score.state is not ScoreState.VALID:
            reason = "recorded_score_missing_or_invalid"
        elif not policy.minimum <= text_value <= policy.maximum or (
            policy.step is not None
            and ((text_value - policy.minimum) / policy.step).denominator != 1
        ):
            reason = "stated_score_outside_policy"
        elif not evidence:
            reason = "score_evidence_missing"
        if reason is not None:
            findings.append(
                _finding(
                    rules,
                    "review.numeric_uncertain",
                    FindingKind.MANUAL,
                    reason,
                    assessments,
                    comments,
                    claims,
                    score.value,
                    evidence,
                )
            )
        elif score.value != text_value:
            findings.append(
                _finding(
                    rules,
                    "review.numeric_mismatch",
                    FindingKind.SCORE_CONFLICT,
                    "explicit_stated_score_differs_from_record",
                    assessments,
                    comments,
                    claims,
                    score.value,
                    evidence,
                )
            )
    return tuple(findings)


def review_comments(
    dataset: Dataset,
    comments: Iterable[CommentInput],
    *,
    batch_id: str,
    student_ids: Iterable[str] | None = None,
    rules: RuleSet = DEFAULT_RULES,
) -> ReviewReport:
    if not isinstance(rules, RuleSet):
        raise AnalysisInputError("ruleset_required")
    scope, records = cohort(dataset, batch_id, student_ids)
    known = {
        record.reference.student_id: record
        for record in dataset.records
        if record.reference.batch_id == batch_id
    }
    selected = {record.reference.student_id for record in records}
    inputs = []
    seen_ids = set()
    seen_cells = set()
    for comment in comments:
        if not isinstance(comment, CommentInput) or not isinstance(comment.assessment, RecordRef):
            raise AnalysisInputError("typed_comment_and_assessment_required")
        if comment.assessment.batch_id != batch_id:
            continue
        expected = known.get(comment.assessment.student_id)
        if expected is None:
            raise AnalysisInputError("unknown_comment_assessment")
        if expected.reference.student_id not in selected:
            continue
        if comment.assessment != expected.reference:
            raise AnalysisInputError("comment_assessment_reference_mismatch")
        classification = classify_comment(comment, rules=rules)
        cell = (comment.assessment, comment.evidence.source_id, comment.evidence.location)
        if comment.comment_id in seen_ids or cell in seen_cells:
            raise AnalysisInputError("duplicate_comment_input")
        seen_ids.add(comment.comment_id)
        seen_cells.add(cell)
        inputs.append((comment, classification))
    inputs.sort(
        key=lambda item: (
            item[0].assessment.student_id,
            item[0].comment_id,
            item[0].evidence.source_id,
            item[0].evidence.location,
        )
    )
    duplicate_index: dict[str, list[CommentRef]] = defaultdict(list)
    classes: dict[RecordRef, list[Classification]] = defaultdict(list)
    findings: dict[RecordRef, list[Finding]] = defaultdict(list)
    for comment, classification in inputs:
        classes[comment.assessment].append(classification)
        findings[comment.assessment].extend(
            _score_findings(
                classification, known[comment.assessment.student_id], dataset.policy, rules
            )
        )
        if comment.text is not None and comment.text.strip():
            key = comment.text.strip() if rules.trim_duplicate_whitespace else comment.text
            duplicate_index[key].append(classification.comment)
    duplicates = []
    for refs in duplicate_index.values():
        if len(refs) < 2:
            continue
        group = DuplicateGroup("review.duplicate", rules.version, rules.fingerprint, tuple(refs))
        duplicates.append(group)
        assessments = tuple(dict.fromkeys(ref.assessment for ref in refs))
        finding = _finding(
            rules,
            group.rule_id,
            FindingKind.DUPLICATE,
            "repeated_text_is_not_itself_an_error",
            assessments,
            group.comments,
        )
        for assessment in assessments:
            findings[assessment].append(finding)
    output = []
    for record in records:
        reference = record.reference
        if not classes[reference]:
            findings[reference].append(
                _finding(
                    rules,
                    "review.missing_comment",
                    FindingKind.MANUAL,
                    "no_comment_supplied",
                    (reference,),
                    (),
                )
            )
        items = tuple(findings[reference])
        state = (
            ReviewState.CANDIDATE
            if any(item.kind is FindingKind.SCORE_CONFLICT for item in items)
            else ReviewState.MANUAL
            if items
            else ReviewState.NO_EXPLICIT_CONFLICT
        )
        output.append(RecordReview(reference, state, tuple(classes[reference]), items))
    return ReviewReport(
        scope,
        rules.version,
        rules.fingerprint,
        rules.status,
        tuple(output),
        tuple(duplicates),
    )
