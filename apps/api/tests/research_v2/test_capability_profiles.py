"""Independent Fraction row oracles, not discover() outputs or model-generated answers."""

import asyncio
import json
from dataclasses import replace
from fractions import Fraction

import pytest
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.capability_profiles.agent import ProfileExecutor, project_selected, run_profiles
from prototypes.capability_profiles.demo import teacher_markdown
from prototypes.capability_profiles.engine import (
    DIMS,
    ProfileBook,
    build_profiles,
    query,
    resolve,
    teacher_view,
)
from prototypes.capability_profiles.synthetic import RECIPES, specification
from prototypes.research_agent_v2.demo import mock_transport

from app.ai.edu.discovery.models import AgentError, Limits


@pytest.fixture(scope="module")
def books():
    return {name: build_profiles(admit(name)) for name in ("five", *RECIPES)}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    async def execute():
        root = tmp_path_factory.mktemp("profile-runs")
        return {name: await run_profiles(name, root / name) for name in ("five", *RECIPES)}

    return asyncio.run(execute())


def row_oracle(name, conditions):
    rows = [[Fraction(x) for x in row] for row in specification(name)["rows"]]

    def hit(row):
        for c in conditions:
            left, right = row[DIMS.index(c.dimension)], c.threshold
            if not {
                "<": left < right,
                "<=": left <= right,
                "==": left == right,
                ">=": left >= right,
                ">": left > right,
            }[c.operator.value]:
                return False
        return True

    target = {i for i, row in enumerate(rows) if hit(row)}
    rest = set(range(len(rows))) - target
    return rows, target, rest


def test_balanced_overall_means_hide_complementary_patterns(books):
    # Each of three 24-person families has symmetric -0.25/0/+0.25 variation.
    rows = specification(RECIPES[0])["rows"]
    assert [sum(Fraction(r[j]) for r in rows) / 72 for j in range(5)] == [Fraction(3)] * 5
    b = books[RECIPES[0]]
    assert len(b.candidates) >= 2
    groups = [c.discovery.payload for c in b.candidates]
    assert any(
        len(p.selection.matches) == 24 and p.selection.conditions[0].threshold == Fraction(9, 4)
        for p in groups
    )
    assert any(
        len(p.selection.matches) == 24 and p.selection.conditions[0].threshold == Fraction(15, 4)
        for p in groups
    )


@pytest.mark.parametrize("name", RECIPES[:2])
def test_members_five_means_distributions_and_actual_witnesses_match_independent_rows(books, name):
    book = books[name]
    for c in book.candidates:
        p = c.discovery.payload
        rows, target, rest = row_oracle(name, p.selection.conditions)
        assert {r.student_id for r in p.selection.matches} == {f"S{i:04}" for i in target}
        assert {r.student_id for r in p.remainder} == {f"S{i:04}" for i in rest}
        view = teacher_view(book, c.profile_id)
        assert view["metrics"]["student_denominator"] == len(rows)
        assert view["metrics"]["target_count"] == len(target)
        assert view["metrics"]["remainder_count"] == len(rest)
        for j, d in enumerate(p.dimensions):
            a = sum(rows[i][j] for i in target) / len(target)
            b = sum(rows[i][j] for i in rest) / len(rest)
            assert (d.target.mean, d.remainder.mean, d.difference) == (a, b, a - b)
            assert sum(f.share.count for f in d.target.distribution) == len(target)
        # One dimension, actual participating records, not arbitrary cohort membership.
        witnesses = resolve(book, c.profile_id, "target.MODELING")
        assert {w.participant_id for w in witnesses} == {f"S{i:04}" for i in target}
        assert all(
            w.source_sha256 == book.source.fingerprint and w.item_id == "I03" for w in witnesses
        )


def test_overlap_is_allowed_without_forcing_a_partition(books):
    book = books[RECIPES[1]]
    groups = [
        set(r.student_id for r in c.discovery.payload.selection.matches) for c in book.candidates
    ]
    # Independent binary factors: two 48-person groups share exactly one 24-person cell.
    assert any(
        len(a) == len(b) == 48 and len(a & b) == 24
        for i, a in enumerate(groups)
        for b in groups[i + 1 :]
    )
    assert len(groups) == len({frozenset(g) for g in groups})
    partitions = {
        frozenset(
            (
                frozenset(c.discovery.payload.selection.matches),
                frozenset(c.discovery.payload.remainder),
            )
        )
        for c in book.candidates
    }
    assert len(partitions) == len(groups)
    assert book.audit["search_attempts"] <= 50


def test_no_typical_group_despite_varying_scores(books):
    rows = specification(RECIPES[2])["rows"]
    # Full 3^4 factorial design: every pair (including modular fifth item) is balanced.
    for a in range(5):
        for b in range(a + 1, 5):
            counts = {}
            for row in rows:
                pair = row[a], row[b]
                counts[pair] = counts.get(pair, 0) + 1
            assert set(counts.values()) == {9}
    assert books[RECIPES[2]].candidates == ()


def test_original_53_people_26_27_and_exact_four_point_difference(books):
    book = books["five"]
    assert len(book.candidates) == 1
    p = book.candidates[0].discovery.payload
    assert len(p.selection.matches) == 26 and len(p.remainder) == 27
    d = next(d for d in p.dimensions if d.dimension == p.outcome_dimension)
    assert (d.target.mean, d.remainder.mean, d.difference) == (5, 1, 4)
    assert teacher_view(book, book.candidates[0].profile_id)["metrics"]["share_total"] == {
        "numerator": 26,
        "denominator": 53,
        "display": "0.4906",
    }


def test_stable_identifiers_and_projection(books):
    a = books[RECIPES[0]]
    b = build_profiles(admit(RECIPES[0]))
    assert [c.profile_id for c in a.candidates] == [c.profile_id for c in b.candidates]
    assert [teacher_view(a, c.profile_id) for c in a.candidates] == [
        teacher_view(b, c.profile_id) for c in b.candidates
    ]


def test_unknown_forged_snapshot_and_metric_are_rejected(books):
    book = books["five"]
    c = book.candidates[0]
    with pytest.raises(AgentError, match="unknown_profile_id"):
        book.verify("forged")
    with pytest.raises(AgentError, match="unknown_profile_metric"):
        resolve(book, c.profile_id, "arbitrary.sql")
    forged = replace(
        c,
        discovery=replace(
            c.discovery, context=replace(c.discovery.context, snapshot_id="other-snapshot")
        ),
    )
    with pytest.raises(AgentError, match="snapshot"):
        ProfileBook(book.source, (forged,), {}).verify(c.profile_id)
    with pytest.raises(AgentError, match="source_replay"):
        corrupt = replace(c, discovery=replace(c.discovery, summary="fabricated"))
        ProfileBook(book.source, (corrupt,), {}).verify(c.profile_id)
    with pytest.raises(ValueError):
        build_profiles(replace(book.source, fingerprint="0" * 64))
    with pytest.raises(AgentError, match="five_dimension"):
        build_profiles(admit("contest13"))


def test_real_http_protocol_selection_values_sources_and_zero_findings(runs):
    for r, book, executor in runs.values():
        assert r["status"] == "completed"
        assert r["model_requests"] <= 6 and r["tool_attempts"] <= 5
        assert r["real_qwen"] == "not_run" and r["actual_tokens"] is None
        assert r["actual_cost_cny"] is None
        assert r["budget"]["request_count"] == r["model_requests"]
        assert all(t["status"] == "accepted" for t in r["tool_ledger"])
        for profile in r["profiles"]:
            assert profile["source_replay_verified"]
            assert profile["selection_origin"] == "http_mock_offline_mechanism"
            assert profile["comment_state"] == "unavailable_not_authorized"
            assert len(profile["dimensions"]) == 5
        for payload in r["native_requests"]:
            text = json.dumps(payload)
            for forbidden in (
                "S0000",
                "witness",
                "locator",
                "source_sha256",
                "rows",
                "oracle",
            ):
                assert forbidden not in text
            assert all(recipe not in text for recipe in RECIPES)
            assert "profile_investigation" in text
        assert project_selected(book, r, executor) == r["profiles"]
    assert len(runs["five"][0]["profiles"]) == 1
    assert runs["five"][0]["profile_stop"] == "investigated_candidates_complete"
    overlap = runs[RECIPES[1]][0]
    assert len(overlap["profiles"]) == 3 and overlap["uninvestigated_candidates"] == 3
    assert overlap["profile_stop"] == "bounded_profiles_selected"
    assert overlap["investigation_status"] == "display_limited_not_exhaustive"
    assert "三张画像展示上限" in teacher_markdown(overlap)
    assert runs[RECIPES[2]][0]["profiles"] == []
    assert runs[RECIPES[2]][0]["profile_stop"] == "no_eligible_profiles"
    assert "暂无符合展示条件" in teacher_markdown(runs[RECIPES[2]][0])
    assert runs[RECIPES[2]][0]["tool_attempts"] == 1  # Only trusted overview, then stop.
    assert (
        runs[RECIPES[0]][0]["tool_ledger"][0]["arguments"]
        != (runs[RECIPES[1]][0]["tool_ledger"][0]["arguments"])
    )


def test_failed_budget_run_never_becomes_successful_profile(tmp_path):
    result, _, _ = asyncio.run(
        run_profiles("five", tmp_path, limits=Limits(model_requests=1, tool_attempts=2))
    )
    assert result["status"] == "failed"
    assert len(result["profiles"]) == 1  # Budget-limited partial result, never a successful run.
    assert result["retained_fact_ids"] == [result["profiles"][0]["verified_fact_ids"][0]]
    assert result["stop_reason"] == "model_budget_exhausted"
    assert "调查未完成" in teacher_markdown(result)
    assert "暂无符合展示条件" not in teacher_markdown(result)
    assert result["model_requests"] == 1 and result["tool_attempts"] == 2
    with pytest.raises(AgentError, match="campaign_already_consumed"):
        asyncio.run(run_profiles("five", tmp_path))


def test_legal_but_unapproved_candidate_query_cannot_bypass_selector(tmp_path):
    def fault(choice, view):
        return {
            "name": "compare_student_groups",
            "arguments": {
                "conditions": [{"item_id": "I01", "operator": "<=", "threshold": "2.74"}],
                "outcomes": ["I02"],
            },
        }

    result, _, _ = asyncio.run(
        run_profiles("five", tmp_path, transport=mock_transport(fault=fault))
    )
    assert result["status"] == "failed" and result["profiles"] == []
    assert result["stop_reason"] == "profile_query_outside_candidates"
    assert result["tool_attempts"] == 1  # No model-proposed tool executed.


def test_projection_rejects_cross_snapshot_unknown_and_tampered_fact(runs):
    result, book, executor = runs["five"]
    with pytest.raises(AgentError, match="profile_scope_mismatch"):
        project_selected(runs[RECIPES[0]][1], result, executor)
    forged = dict(result, findings=[{"fact_id": "fabricated"}])
    with pytest.raises(AgentError, match="unknown_fact_id"):
        project_selected(book, forged, executor)
    fact = executor.facts[result["findings"][0]["fact_id"]]
    original = fact.view["metrics"]["target_count"]
    try:
        fact.view["metrics"]["target_count"] = original + 1
        with pytest.raises(AgentError, match="source_validation_failed"):
            project_selected(book, result, executor)
    finally:
        fact.view["metrics"]["target_count"] = original


def test_runtime_executor_rejects_unsafe_overlap_without_publishing_state(books):
    book = books[RECIPES[1]]
    pairs = [
        (a, b)
        for a in book.candidates
        for b in book.candidates
        if len(
            set(a.discovery.payload.selection.matches) & set(b.discovery.payload.selection.matches)
        )
        == 24
        and len(a.discovery.payload.selection.matches)
        == len(b.discovery.payload.selection.matches)
        == 48
    ]
    assert pairs
    executor = ProfileExecutor(book.source, "overlap-policy-test", Limits(minimum_cell=25))
    a, b = pairs[0]
    args = query(a.discovery)
    executor.execute("compare_student_groups", executor.parse("compare_student_groups", args))
    before = (dict(executor.facts), list(executor.released), executor.output_bytes)
    with pytest.raises(AgentError, match="privacy_limited"):
        executor.execute(
            "compare_student_groups", executor.parse("compare_student_groups", query(b.discovery))
        )
    assert (executor.facts, executor.released, executor.output_bytes) == before
    assert executor.attempt_ledger[-1]["status"] == "rejected"
    with pytest.raises(AgentError, match="disclosure_policy"):
        project_selected(book, {"status": "completed", "findings": []}, executor)
