"""Local semantic, provenance and join checks; a model cannot confer authority."""

from collections import Counter

from .material_contract import Assessment, Meaning, Role, Suggestions, Verdict
from .material_profile import ColumnProfile, MaterialProfile

ROLE_FIELDS: dict[Role, tuple[frozenset[Meaning], ...]] = {
    "programming": (
        frozenset({"student_key", "problem_key"}),
        frozenset({"code", "score", "verdict", "test_result"}),
    ),
    "evaluation": (
        frozenset({"student_key"}),
        frozenset({"dimension_score", "feedback"}),
    ),
    "problem_material": (),
    "reference": (),
    "other": (),
}
_TYPES = {
    "score": {"number"},
    "dimension_score": {"number"},
    "started_at": {"time"},
    "finished_at": {"time"},
    "runtime": {"number", "duration"},
    "duration": {"number", "duration"},
    "memory": {"number", "memory"},
    "code": {"text"},
    "language": {"text"},
    "verdict": {"text", "number"},
    "test_result": {"text", "number"},
    "feedback": {"text"},
    "rubric": {"text"},
    "student_key": {"text", "number"},
    "problem_key": {"text", "number"},
    "test_point": {"text", "number"},
    "dimension": {"text", "number"},
}


def _fits(column: ColumnProfile, meaning: Meaning) -> bool:
    if not column.kinds or not column.kinds <= _TYPES[meaning]:
        return False
    return not (meaning in {"memory", "runtime", "duration"} and column.negative)


def _check_suggestions(profile: MaterialProfile, suggestions: Suggestions) -> None:
    seen = set()

    def check(key: tuple, evidence: list[str], permitted: set[str], required: set[str]):
        if (
            key in seen
            or len(evidence) != len(set(evidence))
            or not required <= set(evidence) <= permitted
        ):
            raise ValueError("invalid_material_suggestion_reference")
        seen.add(key)

    for row in suggestions.roles:
        if row.table not in profile.tables:
            raise ValueError("invalid_material_suggestion_reference")
        table = profile.tables[row.table]
        check(
            ("role", row.table, row.role),
            row.evidence,
            {row.table, table.file, *(c.alias for c in table.columns)},
            {row.table},
        )
    for row in suggestions.fields:
        if row.column not in profile.columns:
            raise ValueError("invalid_material_suggestion_reference")
        table, _ = profile.columns[row.column]
        check(
            ("field", row.column, row.meaning),
            row.evidence,
            {row.column, table.alias, table.file},
            {row.column},
        )
    for row in suggestions.links:
        if row.left not in profile.columns or row.right not in profile.columns:
            raise ValueError("invalid_material_suggestion_reference")
        left, _ = profile.columns[row.left]
        right, _ = profile.columns[row.right]
        if left.file == right.file:
            raise ValueError("invalid_material_suggestion_reference")
        check(
            ("link", row.left, row.right, row.cardinality),
            row.evidence,
            {row.left, row.right, left.alias, right.alias, left.file, right.file},
            {row.left, row.right},
        )


def validate_material(profile: MaterialProfile, suggestions: Suggestions) -> Assessment:
    _check_suggestions(profile, suggestions)
    fields: dict[tuple[str, Meaning], Verdict] = {}
    roles: dict[tuple[str, Role], Verdict] = {}
    links: dict[tuple[str, str, str], Verdict] = {}
    suggested_fields = Counter(row.column for row in suggestions.fields)
    suggested_roles = Counter(row.table for row in suggestions.roles)
    confirmed = {
        table.columns[index].alias: meaning
        for table in profile.tables.values()
        for index, meaning in table.authority.fields
    }

    field_inputs = {
        (row.column, row.meaning): (row.evidence, row.uncertain) for row in suggestions.fields
    }
    for ref, meaning in confirmed.items():
        field_inputs.setdefault((ref, meaning), ([ref], False))
    for (ref, meaning), (evidence, uncertain) in field_inputs.items():
        table, column = profile.columns[ref]
        column_index = profile.locators[ref].column_index
        bounds = next(
            ((low, high) for i, low, high in table.authority.score_ranges if i == column_index),
            None,
        )
        if table.blocked or column.blocked:
            result = Verdict("unsupported", ("parser_diagnostic_blocks_use",), tuple(evidence))
        elif not _fits(column, meaning):
            result = Verdict("unsupported", ("incompatible_or_empty_field",), tuple(evidence))
        elif (
            meaning in {"score", "dimension_score"}
            and bounds
            and column.numeric_range
            and not bounds[0] <= column.numeric_range[0] <= column.numeric_range[1] <= bounds[1]
        ):
            result = Verdict("unsupported", ("outside_trusted_scale",), tuple(evidence))
        elif confirmed.get(ref) == meaning:
            result = Verdict("confirmed", ("trusted_mapping_and_type_checked",), (ref,))
        elif ref in confirmed:
            result = Verdict("unsupported", ("conflicts_with_trusted_mapping",), tuple(evidence))
        elif (
            uncertain
            or suggested_fields[ref] > 1
            or column.blank_header
            or (not column.name_tokens and ref not in profile.header_labels)
        ):
            result = Verdict("needs_confirmation", ("ambiguous_field_semantics",), tuple(evidence))
        else:
            result = Verdict("candidate", ("type_compatible_semantics_unproved",), tuple(evidence))
        fields[ref, meaning] = result

    role_inputs = {
        (row.table, row.role): (row.evidence, row.uncertain) for row in suggestions.roles
    }
    for table in profile.tables.values():
        if table.authority.role:
            role_inputs.setdefault((table.alias, table.authority.role), ([table.alias], False))
    for (ref, role), (evidence, uncertain) in role_inputs.items():
        table = profile.tables[ref]
        available = {
            meaning
            for (column, meaning), result in fields.items()
            if profile.columns[column][0].alias == ref and result.state != "unsupported"
        }
        if table.blocked:
            result = Verdict("unsupported", ("parser_diagnostic_blocks_use",), tuple(evidence))
        elif table.authority.role == role:
            result = Verdict("confirmed", ("trusted_role",), (ref,))
        elif table.authority.role:
            result = Verdict("unsupported", ("conflicts_with_trusted_role",), tuple(evidence))
        elif any(not (options & available) for options in ROLE_FIELDS[role]):
            result = Verdict(
                "needs_confirmation", ("required_role_fields_missing",), tuple(evidence)
            )
        elif uncertain or suggested_roles[ref] > 1:
            result = Verdict("needs_confirmation", ("ambiguous_role",), tuple(evidence))
        else:
            result = Verdict("candidate", ("structural_role_unproved",), tuple(evidence))
        roles[ref, role] = result

    for row in suggestions.links:
        left, lc = profile.columns[row.left]
        right, rc = profile.columns[row.right]
        left_field = fields.get((row.left, "student_key"))
        right_field = fields.get((row.right, "student_key"))
        evidence = tuple(row.evidence)
        if (
            left.blocked
            or right.blocked
            or lc.blocked
            or rc.blocked
            or any(field and field.state == "unsupported" for field in (left_field, right_field))
        ):
            result = Verdict("unsupported", ("key_unavailable",), evidence)
        elif not lc.values or not rc.values or not (set(lc.values) & set(rc.values)):
            result = Verdict("unsupported", ("no_exact_key_overlap",), evidence)
        else:
            reasons = []
            if not left.complete or not right.complete:
                reasons.append("source_coverage_unproved")
            if lc.missing or rc.missing:
                reasons.append("missing_join_keys")
            if rc.distinct != len(rc.values) or (
                row.cardinality == "one_to_one" and lc.distinct != len(lc.values)
            ):
                reasons.append("duplicate_join_keys")
            if set(lc.values) != set(rc.values):
                reasons.append("unmatched_join_keys")
            if (
                not left_field
                or not right_field
                or (left_field.state != "confirmed" or right_field.state != "confirmed")
            ):
                reasons.append("identity_semantics_unproved")
            if not left.authority.identity_namespace or (
                left.authority.identity_namespace != right.authority.identity_namespace
            ):
                reasons.append("identity_namespace_unproved")
            if not all(
                table.authority.role
                and roles[table.alias, table.authority.role].state == "confirmed"
                for table in (left, right)
            ):
                reasons.append("file_role_unproved")
            if row.uncertain:
                reasons.append("model_requests_review")
            result = (
                Verdict("needs_confirmation", tuple(reasons), evidence)
                if reasons
                else Verdict(
                    "confirmed",
                    ("exact_join_verified_in_trusted_namespace",),
                    (row.left, row.right),
                )
            )
        links[row.left, row.right, row.cardinality] = result
    return Assessment(roles, fields, links)
