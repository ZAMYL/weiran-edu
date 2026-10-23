import re
from fractions import Fraction

from ..models import AnalysisInputError, EvidenceKind, EvidenceRef, RecordRef, require_id
from .models import (
    Classification,
    ClassificationState,
    CommentInput,
    CommentRef,
    Context,
    Hit,
    NumericClaim,
    TopicMatch,
)
from .rules import DEFAULT_RULES, RuleSet


def validate_comment(comment: CommentInput, rules: RuleSet) -> CommentRef:
    if not isinstance(comment, CommentInput) or not isinstance(comment.assessment, RecordRef):
        raise AnalysisInputError("typed_comment_and_assessment_required")
    require_id(comment.comment_id)
    if not isinstance(comment.evidence, EvidenceRef) or (
        comment.evidence.kind is not EvidenceKind.COMMENT
        or comment.evidence not in comment.assessment.evidence_refs
    ):
        raise AnalysisInputError("comment_evidence_must_belong_to_assessment")
    for value in (
        comment.assessment.batch_id,
        comment.assessment.student_id,
        comment.assessment.record_id,
        comment.assessment.source_id,
        comment.evidence.source_id,
        comment.evidence.location,
    ):
        require_id(value)
    if comment.text is not None and not isinstance(comment.text, str):
        raise AnalysisInputError("comment_text_must_be_string_or_none")
    if comment.text is not None and len(comment.text) > rules.max_comment_chars:
        raise AnalysisInputError("comment_too_long")
    return CommentRef(comment.assessment, comment.comment_id, comment.evidence)


def literal_hits(text: str, terms: tuple[str, ...], rule_id: str) -> tuple[Hit, ...]:
    hits = []
    for term in terms:
        start = text.find(term)
        while start >= 0:
            hits.append(Hit(rule_id, start, start + len(term)))
            start = text.find(term, start + 1)
    return tuple(sorted(set(hits), key=lambda hit: (hit.start, hit.end, hit.rule_id)))


def _claims(text: str, rules: RuleSet) -> tuple[tuple[NumericClaim, ...], bool]:
    claims = []
    clauses = tuple(re.finditer(r"[^，,。；;!?！？\r\n]+", text))
    all_numeric = bool(clauses)
    for clause in clauses:
        raw = clause.group()
        stripped = raw.strip()
        if not stripped:
            continue
        start = clause.start() + len(raw) - len(raw.lstrip())
        found = False
        for rule in rules.numeric_rules:
            pattern = (
                rf"(?:本次)?{re.escape(rule.label)}(?:评分|得分)(?:为|是|[:：])\s*"
                r"(?P<score>[+-]?[0-9]{1,6}(?:\.[0-9]{1,6})?)\s*分"
            )
            match = re.fullmatch(pattern, stripped)
            if match:
                claims.append(
                    NumericClaim(
                        rule.dimension,
                        Fraction(match["score"]),
                        Hit(rule.rule_id, start, start + len(stripped)),
                    )
                )
                found = True
        all_numeric = all_numeric and found
    return tuple(claims), all_numeric and bool(claims)


def _standalone_context(text: str, hit: Hit) -> bool:
    for clause in re.finditer(r"[^，,。；;!?！？\r\n]+", text):
        if clause.start() <= hit.start and hit.end <= clause.end():
            prefix = text[clause.start() : hit.start].strip()
            suffix = text[hit.end : clause.end()].strip()
            return prefix in {"", "本次", "目前", "但", "但是", "然而", "不过"} and not suffix
    return False


def classify_comment(comment: CommentInput, *, rules: RuleSet = DEFAULT_RULES) -> Classification:
    if not isinstance(rules, RuleSet):
        raise AnalysisInputError("ruleset_required")
    reference = validate_comment(comment, rules)
    text = comment.text or ""
    guards = literal_hits(text, rules.guard_terms, "context.guarded")
    topics = []
    for topic in rules.topics:
        anchors = literal_hits(text, topic.terms, topic.rule_id)
        if not anchors:
            continue
        context_hits = []
        directions = set()
        safe_hits = []
        for rule in rules.contexts:
            if rule.dimension is topic.dimension:
                hits = literal_hits(text, rule.phrases, rule.rule_id)
                if hits:
                    safe = tuple(hit for hit in hits if _standalone_context(text, hit))
                    safe_hits.extend(safe)
                    if safe:
                        directions.add(rule.context)
                    context_hits.extend(hits)
        uncovered = any(
            not any(safe.start <= hit.start and hit.end <= safe.end for safe in safe_hits)
            for hit in context_hits
        )
        if guards or uncovered:
            context = Context.UNCERTAIN
        elif len(directions) > 1:
            context = Context.MIXED
        elif directions:
            context = next(iter(directions))
        else:
            context = Context.MENTION_ONLY
        hits = tuple(
            sorted(
                set((*anchors, *context_hits)), key=lambda hit: (hit.start, hit.end, hit.rule_id)
            )
        )
        topics.append(TopicMatch(topic.dimension, topic.title, context, hits))
    claims, numeric_only = _claims(text, rules)
    state = (
        ClassificationState.EMPTY
        if not text.strip()
        else ClassificationState.CLASSIFIED
        if topics
        else ClassificationState.UNCLASSIFIED
    )
    return Classification(
        reference,
        rules.version,
        rules.fingerprint,
        rules.status,
        state,
        tuple(topics),
        claims,
        guards,
        numeric_only and not guards,
    )
