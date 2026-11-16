"""Independent hand-computable oracles; fictional scores and locators only."""

import json
import sys
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction as F
from pathlib import Path

import pytest

from app.analysis.edu import (
    AnalysisInputError,
    AssessmentRecord,
    Condition,
    EvidenceKind,
    EvidenceRef,
    ScorePolicy,
    prepare_dataset,
)
from app.analysis.edu import (
    Dimension as D,
)
from app.analysis.edu import (
    Operator as Op,
)
from app.analysis.edu.discovery import (
    BoundaryRequest,
    Policy,
    compare_boundary,
    compare_groups,
    discover,
    present,
    sources,
)
from app.analysis.edu.discovery.models import GroupComparison, RareCombination

BATCH = "synthetic-batch"


def make(rows, batch=BATCH):
    return tuple(
        AssessmentRecord(
            batch,
            f"private-student-{i:05}",
            f"private-record-{i}",
            "private-source-path",
            dict(zip(D, row, strict=True)),
            tuple(
                EvidenceRef(
                    "private-source-path", f"private-cell-{i}-{d.name}", EvidenceKind.SCORE, d
                )
                for d in D
            )
            + (
                EvidenceRef("private-source-path", "withheld-secret-comment", EvidenceKind.COMMENT),
                EvidenceRef("foreign-source", "foreign-score", EvidenceKind.SCORE, D.ALGORITHMS),
            ),
        )
        for i, row in enumerate(rows)
    )


def data(rows):
    return prepare_dataset(make(rows), policy=ScorePolicy(step=F(1, 100)))


def run(dataset, **kwargs):
    return discover(dataset, batch_id=BATCH, snapshot_id="snapshot-1", **kwargs)


def pair(result, left=D.ALGORITHMS, right=D.IMPLEMENTATION):
    return next(p for p in result.correlations.pairs if (p.left, p.right) == (left, right))


@pytest.mark.parametrize("ys,expected", [([1, 2, 3, 4], 1), ([4, 3, 2, 1], -1), ([2, 4, 1, 3], 0)])
def test_spearman_independent_oracles(ys, expected):
    dataset = data([(x, y, 3, 3, 3) for x, y in zip([1, 2, 3, 4], ys, strict=True)])
    result = run(dataset, policy=Policy(minimum_samples=2))
    assert len(result.correlations.pairs) == 10
    assert pair(result).coefficient == pytest.approx(expected)
    assert pair(result).paired_count == 4
    assert not pair(result).excluded_records
    relation = [d for d in result.findings if d.type == "dimension_relation"]
    assert len(relation) == (1 if expected else 0)
    if relation:
        assert relation[0].status == "interpret_cautiously"
        assert not present(relation[0])["ranking"]["is_statistical_confidence"]


def test_ties_constants_missing_and_invalid():
    # Average ranks (1.5,1.5,3,4) vs (1,2.5,2.5,4): dot=3.75, variance=4.5 => 5/6.
    dataset = data(
        [
            (1, 1, 3, 3, 3),
            (1, 2, 3, 3, 3),
            (2, 2, 3, 3, 3),
            (3, 3, 3, 3, 3),
            (None, 4, 3, 3, 3),
            (6, 5, 3, 3, 3),
        ]
    )
    result = run(dataset, policy=Policy(minimum_samples=2))
    assert pair(result).coefficient == pytest.approx(5 / 6)
    assert pair(result).paired_count == 4
    assert len(pair(result).excluded_records) == 2
    assert pair(result, D.MODELING, D.EFFICIENCY).state.value == "constant_input"
    assert (result.quality[0].valid, result.quality[0].missing, result.quality[0].invalid) == (
        4,
        1,
        1,
    )
    for d in result.findings:
        present(d)
        if d.type == "dimension_relation":
            assert len(sources(d, "paired_scores")) == d.payload.paired_count


@pytest.mark.parametrize("size", [0, 1, 17, 53, 105, 320, 2000])
def test_variable_scale_constant_data_has_no_forced_findings(size):
    result = run(data([(3, 3, 3, 3, 3)] * size))
    assert result.context.scope.student_count == size
    assert result.findings == ()
    assert all(p.paired_count == size for p in result.correlations.pairs)
    assert all(q.valid == size and q.missing == q.invalid == 0 for q in result.quality)
    assert all(p.coefficient is None for p in result.correlations.pairs)


@pytest.mark.parametrize("size", [17, 53, 105])
def test_data_driven_groups_and_disjoint_comparison(size):
    half = size // 2
    dataset = data([(1, 5, 3, 3, 3)] * half + [(5, 1, 3, 3, 3)] * (size - half))
    result = run(dataset, policy=Policy(maximum_findings=50))
    groups = [d for d in result.findings if d.type == "group_difference"]
    assert groups
    group = next(d for d in groups if d.payload.selection.conditions[0].dimension == D.ALGORITHMS)
    compared = group.payload
    assert isinstance(compared, GroupComparison)
    a, b = compared.dimensions[1].target, compared.dimensions[1].remainder
    assert (a.mean, b.mean) == (5, 1)  # Not target-vs-class mean, which contains target itself.
    assert (a.valid_count, b.valid_count) == (half, size - half)
    assert compared.dimensions[1].difference == 4
    assert compared.dimensions[0].condition_defined
    assert not compared.dimensions[1].condition_defined
    assert present(group)["metrics"]["student_denominator"] == size
    assert len(sources(group, "target.IMPLEMENTATION")) == half


def test_condition_only_difference_is_not_new_discovery():
    result = run(data([(1, 3, 3, 3, 3)] * 10 + [(5, 3, 3, 3, 3)] * 10))
    assert result.findings == ()
    assert all(a.outcome == "insufficient_support_or_effect" for a in result.search_audit)


def test_group_missing_denominators_and_actual_sources():
    dataset = data(
        [(1, 5, 3, 3, 3)] * 6
        + [(1, None, 3, 3, 3)]
        + [(5, 1, 3, 3, 3)] * 6
        + [(5, 6, 3, 3, 3)]
        + [(None, 4, 3, 3, 3)]
    )
    c = compare_groups(dataset, batch_id=BATCH, conditions=(Condition(D.ALGORITHMS, Op.LE, 1),))
    assert (len(c.selection.matches), len(c.remainder), len(c.selection.unevaluable_records)) == (
        7,
        7,
        1,
    )
    assert c.selection.matched_share.proportion == F(7, 15)
    assert c.selection.matched_among_eligible.proportion == F(1, 2)
    assert c.dimensions[1].target.mean == 5
    assert c.dimensions[1].target.valid_count == 6
    assert c.dimensions[1].target.missing_count == 1
    assert c.dimensions[1].remainder.invalid_count == 1
    result = run(dataset, policy=Policy(maximum_findings=50))
    discovery = next(
        d
        for d in result.findings
        if d.type == "group_difference"
        and d.payload.selection.conditions == (Condition(D.ALGORITHMS, Op.LE, 1),)
    )
    refs = sources(discovery, "target.IMPLEMENTATION")
    assert len(refs) == 6
    assert {r.student_id for r, _ in refs} == {f"private-student-{i:05}" for i in range(6)}
    assert all(
        e.dimension == D.IMPLEMENTATION
        and e.kind == EvidenceKind.SCORE
        and e.source_id == r.source_id
        for r, es in refs
        for e in es
    )
    # Internal forged count/mean must fail rather than project believable numbers.
    dims = list(discovery.payload.dimensions)
    dims[1] = replace(dims[1], target=replace(dims[1].target, valid_count=7))
    with pytest.raises(AnalysisInputError, match="inconsistent_discovery_statistics"):
        present(replace(discovery, payload=replace(discovery.payload, dimensions=tuple(dims))))
    with pytest.raises(AnalysisInputError, match="unknown_discovery_metric"):
        sources(discovery, "invented")


@pytest.mark.parametrize(
    "size,rare_count,pattern",
    [(17, 1, "individual_review"), (53, 2, "small_group_review"), (105, 5, "group_pattern")],
)
def test_explainable_rare_contrasts(size, rare_count, pattern):
    # Q1=Q3=median=0; 1.5-IQR and 3-IQR fences=0; contrast4 strictly outside.
    dataset = data([(3, 3, 3, 3, 3)] * (size - rare_count) + [(5, 1, 3, 3, 3)] * rare_count)
    result = run(dataset, policy=Policy(maximum_findings=50))
    rare = next(
        d
        for d in result.findings
        if isinstance(d.payload, RareCombination)
        and (d.payload.left, d.payload.right) == (D.ALGORITHMS, D.IMPLEMENTATION)
    )
    assert (rare.payload.q1, rare.payload.q3, rare.payload.median) == (0, 0, 0)
    assert len(rare.payload.matches) == rare_count
    assert rare.payload.pattern == pattern
    assert rare.status == "interpret_cautiously"
    assert rare.ranking.robustness.value == 1
    assert present(rare)["metrics"]["share_pairs"] == {
        "numerator": F(rare_count, size).numerator,
        "denominator": F(rare_count, size).denominator,
        "display": f"{rare_count / size:.4f}",
    }
    assert len(sources(rare, "rare_members")) == rare_count
    assert len(sources(rare, "pair_reference")) == size


def test_rare_small_sample_and_data_quality_not_anomalies():
    assert run(data([(3, 3, 3, 3, 3), (5, 1, 3, 3, 3)])).findings == ()
    result = run(data([(3, 3, 3, 3, 3)] * 16 + [(6, 3, 3, 3, 3)]))
    assert result.findings == ()
    assert result.quality[0].invalid == 1


def boundary_request(threshold=F(11, 4)):
    return BoundaryRequest(
        (Condition(D.ALGORITHMS, Op.LE, threshold), Condition(D.STRATEGY, Op.GE, 4)), D.ALGORITHMS
    )


def test_known_53_fixture_exact_boundary_oracle():
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
    from edu_demo_fixtures import make_fixture

    rows = make_fixture(53, 42).scores
    # Independent Decimal predicate, and explicit hand/audited count requirements.
    strict = [i for i, row in enumerate(rows) if row[0] < Decimal("2.75") and row[4] >= 4]
    inclusive = [i for i, row in enumerate(rows) if row[0] <= Decimal("2.75") and row[4] >= 4]
    assert len(strict) == 4
    assert len(inclusive) == 5
    assert set(inclusive) - set(strict) == {1}
    dataset = data(rows)
    compared = compare_boundary(dataset, batch_id=BATCH, request=boundary_request())
    assert (len(compared.strict.matches), len(compared.inclusive.matches)) == (4, 5)
    assert [r.student_id for r in compared.added] == ["private-student-00001"]
    assert compared.strict.matched_share.proportion == F(4, 53)
    assert compared.inclusive.matched_share.proportion == F(5, 53)
    result = run(dataset, boundaries=(boundary_request(),), policy=Policy(maximum_findings=50))
    finding = next(d for d in result.findings if d.type == "boundary_sensitivity")
    assert present(finding)["metrics"]["added_count"] == 1
    assert len(sources(finding, "added_members")) == 1
    # Display rounding is downstream and never fed back into the predicate.
    assert round(float(F(11, 4)), 0) == 3
    assert len(compared.strict.matches) == 4


def test_boundary_missing_invalid_zero_and_precision():
    dataset = data(
        [
            (2.74, 3, 3, 3, 4),
            (2.75, 3, 3, 3, 4),
            (2.76, 3, 3, 3, 4),
            (None, 3, 3, 3, 4),
            (2.751, 3, 3, 3, 4),
            (2, 3, 3, 3, None),
        ]
    )
    result = compare_boundary(dataset, batch_id=BATCH, request=boundary_request())
    assert (
        len(result.strict.matches),
        len(result.inclusive.matches),
        result.strict.eligible_count,
    ) == (1, 2, 3)
    assert len(result.strict.unevaluable_records) == 3
    assert result.inclusive.matched_among_eligible.proportion == F(2, 3)
    assert result.inclusive.matched_share.proportion == F(1, 3)
    for rows in ([], [(None, 3, 3, 3, 4)]):
        empty = compare_boundary(data(rows), batch_id=BATCH, request=boundary_request())
        assert empty.status == "insufficient_evidence"
        assert empty.inclusive.matched_among_eligible.proportion is None
    assert not run(data([(3, 3, 3, 3, 3)]), boundaries=(boundary_request(),)).findings
    with pytest.raises(AnalysisInputError, match="threshold_outside"):
        compare_boundary(dataset, batch_id=BATCH, request=boundary_request(F(2751, 1000)))


def test_batch_scope_determinism_privacy_and_content_ids():
    records = make([(1, 5, 3, 3, 3)] * 10 + [(5, 1, 3, 3, 3)] * 10)
    foreign = make([(3, 3, 3, 3, 3)] * 17, "other-batch")
    one = prepare_dataset(records, policy=ScorePolicy(step=F(1, 100)))
    mixed = prepare_dataset(tuple(reversed(records + foreign)), policy=ScorePolicy(step=F(1, 100)))
    a, b = run(one), run(mixed)
    assert a == b == run(one)
    assert all(r.batch_id == BATCH for d in a.findings for s in d.sources for r in s.records)
    assert (
        run(one, student_ids=tuple(r.student_id for r in records[:5])).context.scope.student_count
        == 5
    )
    text = json.dumps([present(d) for d in a.findings], ensure_ascii=False)
    assert not any(
        token in text
        for token in (
            "private-student",
            "private-record",
            "private-source",
            "private-cell",
            "withheld-secret",
            "foreign-score",
        )
    )
    changed = run(data([(1, 4, 3, 3, 3)] * 10 + [(5, 1, 3, 3, 3)] * 10))
    assert {d.discovery_id for d in a.findings}.isdisjoint(d.discovery_id for d in changed.findings)
    assert all(0 <= d.ranking.score <= 1 for d in a.findings)
    assert list(a.findings) == sorted(
        a.findings, key=lambda d: (-d.ranking.score, d.type, d.discovery_id)
    )


def test_budgets_validation_and_no_infinite_enumeration():
    dataset = data(
        [
            (1 + i % 5, 1 + (i * 3) % 5, 1 + (i * 2) % 5, 1 + (i * 4) % 5, 1 + (i * 7) % 5)
            for i in range(105)
        ]
    )
    result = run(dataset, policy=Policy(search_budget=13, maximum_findings=2))
    assert len(result.search_audit) == 13
    assert len(result.findings) <= 2
    assert len(run(dataset, policy=Policy(search_budget=50)).search_audit) == 50
    with pytest.raises(AnalysisInputError, match="budget"):
        run(dataset, policy=Policy(maximum_students=17))
    with pytest.raises(AnalysisInputError):
        Policy(search_budget=51)
    with pytest.raises(AnalysisInputError):
        Policy(correlation_threshold=float("nan"))
    with pytest.raises(AnalysisInputError, match="cent_score_policy"):
        run(prepare_dataset(make([(3, 3, 3, 3, 3)]), policy=ScorePolicy()))
    with pytest.raises(AnalysisInputError, match="boundary_budget"):
        run(dataset, boundaries=(boundary_request(),) * 21)
    with pytest.raises(AnalysisInputError, match="distinct_condition"):
        compare_groups(dataset, batch_id=BATCH, conditions=(Condition(D.ALGORITHMS, Op.LT, 3),) * 2)


def test_explicit_and_groups_and_zero_matches():
    dataset = data(
        [(1, 5, 3, 3, 5)] * 5
        + [(1, 1, 3, 3, 1)] * 5
        + [(5, 1, 3, 3, 5)] * 5
        + [(5, 1, 3, 3, 1)] * 2
    )
    conditions = (Condition(D.ALGORITHMS, Op.LE, 1), Condition(D.STRATEGY, Op.GE, 5))
    c = compare_groups(dataset, batch_id=BATCH, conditions=conditions)
    assert (len(c.selection.matches), len(c.remainder)) == (5, 12)
    assert c.dimensions[1].difference == 4
    assert (c.dimensions[1].target.valid_count, c.dimensions[1].remainder.valid_count) == (5, 12)
    assert c.dimensions[0].condition_defined and c.dimensions[4].condition_defined
    empty = compare_groups(dataset, batch_id=BATCH, conditions=(Condition(D.ALGORITHMS, Op.LT, 1),))
    assert not empty.selection.matches
    assert len(empty.remainder) == 17
    assert empty.dimensions[1].difference is None
    assert empty.selection.matched_share.proportion == 0


def test_source_query_rejects_same_scope_but_wrong_participants():
    result = run(
        data([(1, 5, 3, 3, 3)] * 6 + [(1, None, 3, 3, 3)] + [(5, 1, 3, 3, 3)] * 7),
        policy=Policy(maximum_findings=50),
    )
    d = next(d for d in result.findings if d.type == "group_difference")
    original = next(s for s in d.sources if s.metric_key == "target.IMPLEMENTATION")
    forged = replace(original, records=d.context.scope.records)
    changed = replace(d, sources=tuple(forged if s is original else s for s in d.sources))
    with pytest.raises(AnalysisInputError, match="source_mismatch"):
        sources(changed, original.metric_key)


def test_reproducible_synthetic_demo_has_boundary_and_no_locators():
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
    from edu_discovery_demo import demo, examples

    a, b = demo(), demo()
    assert a == b
    finding = next(d for d in a["findings"] if d["type"] == "boundary_sensitivity")
    assert (finding["metrics"]["strict_count"], finding["metrics"]["inclusive_count"]) == (4, 5)
    assert "record-" not in json.dumps(a)
    cards = examples()["examples"]
    assert {c["type"] for c in cards} == {
        "dimension_relation",
        "group_difference",
        "rare_combination",
        "boundary_sensitivity",
    }
    assert (
        next(c for c in cards if c["type"] == "dimension_relation")["metrics"]["rho"]["value"] == -1
    )


@pytest.mark.parametrize("size", [17, 53, 105, 2000])
def test_nonconstant_multiscale_exact_relations(size):
    # Every row obeys y=6-x, z=x; repetitions affect tied ranks but not perfect monotonicity.
    rows = [(1 + i % 5, 5 - i % 5, 1 + i % 5, 3, 3) for i in range(size)]
    result = run(data(rows), policy=Policy(search_budget=2))
    assert pair(result).coefficient == pytest.approx(-1)
    assert pair(result, D.ALGORITHMS, D.MODELING).coefficient == pytest.approx(1)
    assert pair(result).paired_count == size
    assert len(result.search_audit) == 2


def test_nonzero_iqr_fences_and_both_tails():
    contrasts = [-2, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 2, 4]
    rows = [(1 + max(d, 0), 1 + max(-d, 0), 3, 3, 3) for d in contrasts]
    result = run(data(rows), policy=Policy(maximum_findings=50))
    rares = [
        d
        for d in result.findings
        if isinstance(d.payload, RareCombination)
        and (d.payload.left, d.payload.right) == (D.ALGORITHMS, D.IMPLEMENTATION)
    ]
    assert len(rares) == 2
    assert {d.payload.side for d in rares} == {"lower", "upper"}
    for d in rares:
        assert (d.payload.q1, d.payload.q3, d.payload.median) == (0, 1, 0)
        assert (d.payload.lower_fence, d.payload.upper_fence) == (F(-3, 2), F(5, 2))
        assert len(d.payload.matches) == 1
        assert len(sources(d, "pair_reference")) == 17


def test_ranking_components_and_robustness_are_observations_not_confidence():
    result = run(data([(1, 5, 3, 3, 3)] * 20 + [(5, 1, 3, 3, 3)] * 20))
    d = next(d for d in result.findings if d.type == "group_difference")
    assert d.ranking.effect == d.ranking.completeness == d.ranking.support_component == 1
    assert (
        d.ranking.robustness.planned,
        d.ranking.robustness.evaluated,
        d.ranking.robustness.passed,
    ) == (3, 3, 3)
    assert d.ranking.score == 1
    assert d.status == "displayable"
    assert not present(d)["public_display_allowed"]


def test_wrong_dimension_and_rare_sources_rejected():
    result = run(data([(3, 3, 3, 3, 3)] * 16 + [(5, 1, 3, 3, 3)]))
    d = next(d for d in result.findings if d.type == "rare_combination")
    first = d.sources[0]
    altered = replace(first, dimensions=(D.STRATEGY,))
    forged = replace(d, sources=(altered, *d.sources[1:]))
    with pytest.raises(AnalysisInputError, match="source_mismatch"):
        sources(forged, first.metric_key)
    # Replace one real pair member with an existing but excluded participant.
    rows = [(3, 3, 3, 3, 3)] * 16 + [(5, 1, 3, 3, 3), (None, None, None, None, None)]
    result = run(data(rows), policy=Policy(maximum_findings=50))
    d = next(d for d in result.findings if d.type == "rare_combination")
    reference = d.sources[1]
    wrong = (*reference.records[1:], d.context.scope.records[-1])
    altered = replace(reference, records=wrong)
    with pytest.raises(AnalysisInputError, match="source_mismatch"):
        sources(replace(d, sources=(d.sources[0], altered)), "pair_reference")


def test_wrong_distribution_denominator_and_snapshot_identity():
    dataset = data([(1, 5, 3, 3, 3)] * 8 + [(5, 1, 3, 3, 3)] * 9)
    result = run(dataset)
    d = next(d for d in result.findings if d.type == "group_difference")
    dims = list(d.payload.dimensions)
    stats = dims[1].target
    frequencies = list(stats.distribution)
    frequencies[0] = replace(frequencies[0], share=replace(frequencies[0].share, denominator=1))
    dims[1] = replace(dims[1], target=replace(stats, distribution=tuple(frequencies)))
    with pytest.raises(AnalysisInputError, match="inconsistent_discovery_statistics"):
        present(replace(d, payload=replace(d.payload, dimensions=tuple(dims))))
    other = discover(dataset, batch_id=BATCH, snapshot_id="different-snapshot")
    assert {d.discovery_id for d in result.findings}.isdisjoint(
        d.discovery_id for d in other.findings
    )
    assert all(d.context.snapshot_id == "different-snapshot" for d in other.findings)
    with pytest.raises(AnalysisInputError, match="unknown_student"):
        run(dataset, student_ids=("foreign-student",))


def test_observed_boundary_with_no_matching_peers_is_not_a_discovery():
    dataset = data([(2.75, 3, 3, 3, 1)] * 17)
    compared = compare_boundary(dataset, batch_id=BATCH, request=boundary_request())
    assert compared.observed_boundary
    assert not compared.strict.matches and not compared.inclusive.matches
    assert compared.strict.eligible_count == 17
    assert not run(dataset, boundaries=(boundary_request(),)).findings


def test_cli_runs_in_fresh_interpreter_without_inherited_import_path(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.delenv("PYTHONPATH", raising=False)
    script = Path(__file__).resolve().parents[4] / "scripts" / "edu_discovery_demo.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    output = json.loads(result.stdout)
    assert output["synthetic_only"]
    assert len(output["examples"]) == 4
    assert output["known_53"]["student_count"] == 53


def test_twenty_students_ineligible_rule_cannot_reserve_qualified_partition():
    # Independent oracle: B difference 2.90-3.10=-0.20 is below 0.5;
    # defining groups by B makes A an outcome with 1-5=-4, n=10 per side.
    dataset = data([(1, F(29, 10), 3, 3, 3)] * 10 + [(5, F(31, 10), 3, 3, 3)] * 10)
    result = run(dataset, policy=Policy(maximum_findings=50))
    groups = [d for d in result.findings if d.type == "group_difference"]
    assert len(groups) == 1
    group = groups[0]
    assert group.payload.selection.conditions == (Condition(D.IMPLEMENTATION, Op.LE, F(29, 10)),)
    assert group.payload.outcome_dimension is D.ALGORITHMS
    outcome = group.payload.dimensions[0]
    assert (outcome.target.mean, outcome.remainder.mean, outcome.difference) == (1, 5, -4)
    assert (outcome.target.valid_count, outcome.remainder.valid_count) == (10, 10)
    assert result.search_audit[0].outcome == "insufficient_support_or_effect"
    assert result.search_audit[1].outcome == "insufficient_support_or_effect"
    assert result.search_audit[2].outcome == "qualified"
    assert result.search_audit[3].outcome == "duplicate_partition"
    assert present(group)["metrics"]["outcome_dimension"] == "ALGORITHMS"
    assert len(sources(group, "target.ALGORITHMS")) == 10


def test_same_partition_distinguishes_filter_and_each_outcome_but_not_reverse_duplicates():
    # All three varying dimensions split the same 20 students into 10/10.
    # Three singleton filter sets each have two outcomes: 6 facts.
    # Three two-dimension filter sets each have one outcome: 3 facts.
    # Reverse orientation and repeated equivalent AND partitions add no facts.
    dataset = data([(1, 1, 5, 3, 3)] * 10 + [(5, 5, 1, 3, 3)] * 10)
    result = run(dataset, policy=Policy(search_budget=50, maximum_findings=50))
    groups = [d for d in result.findings if d.type == "group_difference"]
    expected = {
        (frozenset([D.ALGORITHMS]), D.IMPLEMENTATION),
        (frozenset([D.ALGORITHMS]), D.MODELING),
        (frozenset([D.IMPLEMENTATION]), D.ALGORITHMS),
        (frozenset([D.IMPLEMENTATION]), D.MODELING),
        (frozenset([D.MODELING]), D.ALGORITHMS),
        (frozenset([D.MODELING]), D.IMPLEMENTATION),
        (frozenset([D.ALGORITHMS, D.IMPLEMENTATION]), D.MODELING),
        (frozenset([D.ALGORITHMS, D.MODELING]), D.IMPLEMENTATION),
        (frozenset([D.IMPLEMENTATION, D.MODELING]), D.ALGORITHMS),
    }
    assert len(groups) == 9
    actual = [
        (
            frozenset(c.dimension for c in d.payload.selection.conditions),
            d.payload.outcome_dimension,
        )
        for d in groups
    ]
    assert set(actual) == expected
    assert len(set(actual)) == len(actual)
    assert len({d.discovery_id for d in groups}) == 9
    assert len(result.search_audit) == 18 <= 50
    assert any(a.outcome == "duplicate_partition" for a in result.search_audit)
    for group in groups:
        outcome = next(
            d for d in group.payload.dimensions if d.dimension is group.payload.outcome_dimension
        )
        assert not outcome.condition_defined
        assert abs(outcome.difference) == 4
        assert (outcome.target.valid_count, outcome.remainder.valid_count) == (10, 10)
        assert present(group)["metrics"]["outcome_dimension"] == outcome.dimension.name
    truncated = run(dataset, policy=Policy(search_budget=50, maximum_findings=1))
    assert truncated.qualified_count == result.qualified_count
    assert len(truncated.findings) == 1


@pytest.mark.parametrize("size", [17, 53, 105])
@pytest.mark.parametrize("missing", [False, True])
def test_semantic_dedup_across_sizes_missing_scores_sources_and_stable_sort(size, missing):
    half = size // 2
    rows = [(1, F(29, 10), 3, 3, 3)] * half + [(5, F(31, 10), 3, 3, 3)] * (size - half)
    if missing:
        rows[0] = (None, F(29, 10), 3, 3, 3)
        rows[half] = (None, F(31, 10), 3, 3, 3)
    dataset = data(rows)
    result = run(dataset, policy=Policy(maximum_findings=50))
    groups = [d for d in result.findings if d.type == "group_difference"]
    assert len(groups) == 1
    group = groups[0]
    assert group.payload.outcome_dimension is D.ALGORITHMS
    a = group.payload.dimensions[0]
    assert a.difference == -4
    assert (a.target.valid_count, a.remainder.valid_count) == (
        half - int(missing),
        size - half - int(missing),
    )
    assert len(group.payload.selection.matches) == half
    assert len(group.payload.remainder) == size - half
    expected_ids = {f"private-student-{i:05}" for i in range(int(missing), half)}
    assert {r.student_id for r, _ in sources(group, "target.ALGORITHMS")} == expected_ids
    assert group.ranking.completeness == F(size - 2 * int(missing), size)
    reordered = prepare_dataset(tuple(reversed(make(rows))), policy=ScorePolicy(step=F(1, 100)))
    assert (
        result
        == run(reordered, policy=Policy(maximum_findings=50))
        == run(dataset, policy=Policy(maximum_findings=50))
    )
    assert list(result.findings) == sorted(
        result.findings, key=lambda d: (-d.ranking.score, d.type, d.discovery_id)
    )
    assert len(result.search_audit) <= result.policy.search_budget


def test_ineligible_rules_still_consume_search_budget_without_forcing_more_search():
    dataset = data([(1, F(29, 10), 3, 3, 3)] * 10 + [(5, F(31, 10), 3, 3, 3)] * 10)
    limited = run(dataset, policy=Policy(search_budget=2, maximum_findings=50))
    assert len(limited.search_audit) == 2
    assert not any(d.type == "group_difference" for d in limited.findings)
    sufficient = run(dataset, policy=Policy(search_budget=3, maximum_findings=50))
    assert len(sufficient.search_audit) == 3
    assert len([d for d in sufficient.findings if d.type == "group_difference"]) == 1


def test_same_filter_partition_keeps_per_outcome_samples_rank_and_sources():
    rows = [(1, 1, 5, 3, 3)] * 10 + [(5, 5, 1, 3, 3)] * 10
    rows[0] = (1, 1, None, 3, 3)
    result = run(data(rows), policy=Policy(maximum_findings=50))
    groups = [
        d
        for d in result.findings
        if d.type == "group_difference"
        and d.payload.selection.conditions == (Condition(D.ALGORITHMS, Op.LE, 1),)
    ]
    assert len(groups) == 2
    by_outcome = {d.payload.outcome_dimension: d for d in groups}
    implementation = by_outcome[D.IMPLEMENTATION]
    modeling = by_outcome[D.MODELING]
    assert implementation.payload.dimensions[1].difference == -4
    assert modeling.payload.dimensions[2].difference == 4
    assert implementation.ranking.completeness == 1
    assert modeling.ranking.completeness == F(19, 20)
    assert implementation.ranking.support_count == 10
    assert modeling.ranking.support_count == 9
    assert len(sources(implementation, "target.IMPLEMENTATION")) == 10
    modeling_refs = sources(modeling, "target.MODELING")
    assert len(modeling_refs) == 9
    assert "private-student-00000" not in {r.student_id for r, _ in modeling_refs}
    assert all(e.dimension is D.MODELING for _, refs in modeling_refs for e in refs)
    assert not present(modeling)["public_display_allowed"]


def test_outcome_metadata_cannot_promote_a_condition_dimension():
    dataset = data([(1, 5, 3, 3, 3)] * 10 + [(5, 1, 3, 3, 3)] * 10)
    standalone = compare_groups(
        dataset, batch_id=BATCH, conditions=(Condition(D.ALGORITHMS, Op.LE, 1),)
    )
    assert standalone.outcome_dimension is None
    group = next(d for d in run(dataset).findings if d.type == "group_difference")
    condition = group.payload.selection.conditions[0].dimension
    with pytest.raises(AnalysisInputError, match="invalid_group_outcome_dimension"):
        present(replace(group, payload=replace(group.payload, outcome_dimension=condition)))


def test_present_carries_dedup_affected_and_why():
    dataset = data([(1, 5, 3, 3, 3)] * 8 + [(5, 1, 3, 3, 3)] * 9)
    result = run(dataset, policy=Policy(minimum_samples=2))
    card = present(result.findings[0])
    assert card["dedup_key"].startswith(card["type"] + "|")
    assert 0 < card["affected"]["count"] <= card["affected"]["total"]
    assert card["why"].endswith("。")
    assert "target_count" not in card["why"]
