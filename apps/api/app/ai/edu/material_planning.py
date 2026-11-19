"""Plan calls to existing modules from verified, colocated, authorized evidence."""

from .material_contract import Assessment, Capability, CapabilityRule
from .material_profile import MaterialProfile

DEFAULT_RULES = (
    CapabilityRule(
        "problem_score_distribution",
        "AssessmentReader.problem_patterns",
        "programming",
        frozenset({"problem_key", "score"}),
        frozenset({"teacher_analysis"}),
    ),
    CapabilityRule(
        "code_error_patterns",
        "DS-03 analyze",
        "programming",
        frozenset({"student_key", "problem_key", "code", "verdict"}),
        frozenset({"teacher_analysis", "replayable_source", "supported_code"}),
    ),
    CapabilityRule(
        "test_failure_patterns",
        "AssessmentReader.problem_patterns",
        "programming",
        frozenset({"problem_key", "test_point", "test_result"}),
        frozenset({"teacher_analysis"}),
    ),
    CapabilityRule(
        "individual_submission_evidence",
        "AssessmentReader.student",
        "programming",
        frozenset({"student_key", "problem_key"}),
        frozenset({"individual_evidence", "replayable_source"}),
    ),
    CapabilityRule(
        "five_dimension_review",
        "canonical_assessment.readiness.assess",
        "evaluation",
        frozenset({"student_key", "dimension_score"}),
        frozenset({"verified_five_dimensions", "teacher_analysis", "replayable_source"}),
    ),
    CapabilityRule(
        "c01_investigation",
        "C-01 investigate_programming",
        "programming",
        frozenset({"student_key", "problem_key", "verdict"}),
        frozenset({"c01_admitted", "replayable_source", "teacher_analysis"}),
    ),
    CapabilityRule(
        "linked_programming_evaluation",
        "canonical_assessment.readiness.assess",
        "programming",
        frozenset({"student_key", "problem_key", "score"}),
        frozenset({"teacher_analysis", "replayable_source"}),
        "evaluation",
    ),
)


def plan_capabilities(
    profile: MaterialProfile,
    assessment: Assessment,
    rules: tuple[CapabilityRule, ...] = DEFAULT_RULES,
) -> tuple[Capability, ...]:
    results = []
    for rule in rules:
        applicable = [
            (ref, verdict)
            for (ref, role), verdict in assessment.roles.items()
            if role == rule.role and verdict.state != "unsupported"
        ]
        if not applicable:
            results.append(
                Capability(rule.name, rule.module, None, False, (), (f"role:{rule.role}",), False)
            )
        for ref, verdict in applicable:
            table = profile.tables[ref]
            reasons = []
            evidence = []
            review = verdict.state != "confirmed"
            if review:
                reasons.append(f"confirmed_role:{rule.role}")
            else:
                evidence.append(ref)
            if not table.complete:
                reasons.append("complete_source_coverage")
            gate_sources = dict(table.authority.gates)
            for gate in sorted(rule.gates):
                if not gate_sources.get(gate):
                    reasons.append(f"gate:{gate}")
                else:
                    evidence.append(gate_sources[gate])
            coobserved = None
            for meaning in sorted(rule.fields):
                choices = [
                    column
                    for column in table.columns
                    if (column.alias, meaning) in assessment.fields
                    and assessment.fields[column.alias, meaning].state == "confirmed"
                ]
                if len(choices) != 1:
                    reasons.append(f"confirmed_field:{meaning}")
                    review |= len(choices) > 1 or any(
                        (v := assessment.fields.get((c.alias, meaning)))
                        and v.state in {"candidate", "needs_confirmation"}
                        for c in table.columns
                    )
                    continue
                column = choices[0]
                evidence.append(column.alias)
                coobserved = (
                    column.present_rows if coobserved is None else coobserved & column.present_rows
                )
            if coobserved == frozenset():
                reasons.append("no_complete_evidence_rows")
            if rule.requires_link_to:
                options = []
                for (left, right, _), link in assessment.links.items():
                    ltable, rtable = profile.columns[left][0], profile.columns[right][0]
                    other = (
                        rtable if ltable.alias == ref else ltable if rtable.alias == ref else None
                    )
                    if other and (other.alias, rule.requires_link_to) in assessment.roles:
                        options.append((other, link, right if ltable.alias == ref else left))
                verified = []
                for other, link, target_key in options:
                    permission = dict(other.authority.gates).get("teacher_analysis")
                    candidates = [
                        c
                        for c in other.columns
                        if (v := assessment.fields.get((c.alias, "dimension_score")))
                        and v.state == "confirmed"
                    ]
                    if link.state == "confirmed" and permission and len(candidates) == 1:
                        key = profile.columns[target_key][1]
                        if key.present_rows & candidates[0].present_rows:
                            verified.append((*link.evidence, candidates[0].alias, permission))
                if verified:
                    evidence.extend(item for source in verified for item in source)
                else:
                    reasons.append(f"authorized_verified_link:{rule.requires_link_to}")
                    review |= any(link.state != "unsupported" for _, link, _ in options)
            results.append(
                Capability(
                    rule.name,
                    rule.module,
                    ref,
                    not reasons,
                    tuple(dict.fromkeys(evidence)),
                    tuple(reasons),
                    bool(review),
                )
            )
    return tuple(results)
