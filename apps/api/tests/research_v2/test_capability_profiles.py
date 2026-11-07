"""Independent Fraction row oracles, not discover() outputs or model-generated answers."""

import asyncio
import json
from dataclasses import replace
from fractions import Fraction

import pytest
from prototypes.adaptive_assessment.synthetic import admit
from prototypes.capability_profiles.agent import project_selected, run_profiles
from prototypes.capability_profiles.demo import teacher_markdown
from prototypes.capability_profiles.engine import (
    DIMS,
    ProfileBook,
    build_profiles,
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
    assert result["status"] == "failed" and result["profiles"] == []
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


@pytest.mark.parametrize("name", ("five", *RECIPES[:2]))
def test_readable_titles_and_numeric_claims_follow_verified_summary(runs, name):
    result, book, _ = runs[name]
    for profile in result["profiles"]:
        assert not any(op in profile["title"] for op in ("<", ">", "≤", "≥", "="))
        assert "评分相对" in profile["title"]
        assert "分的参评群体" not in profile["title"]
        verified = book.verify(profile["profile_id"]).discovery
        c = verified.payload
        source_keys = {s.metric_key for s in verified.sources}
        details = profile["explanation"]
        assert details["origin"] == "rule_generated_from_verified_statistics"
        assert details["model_generated"] is False
        assert 1 <= len(details["highlights"]) <= 2
        assert (
            f"本次{c.selection.scope.student_count}人中，{len(c.selection.matches)}人"
            in profile["summary"]
        )
        assert f"其余可判定的{len(c.remainder)}人" in profile["summary"]
        for f in details["highlights"]:
            d = next(d for d in c.dimensions if d.dimension.name == f["dimension"])
            assert not d.condition_defined
            assert f["role"] == "non_condition_difference" and not f["new_agent_fact"]
            for key, expected in (
                ("target_mean", d.target.mean),
                ("remainder_mean", d.remainder.mean),
                ("difference", d.difference),
            ):
                assert Fraction(f[key]["numerator"], f[key]["denominator"]) == expected
            assert f"{float(d.target.mean):.2f}分" in profile["summary"]
            assert f"{float(d.remainder.mean):.2f}分" in profile["summary"]
            sign = "高" if d.difference > 0 else "低"
            assert f"{sign}{float(abs(d.difference)):.2f}分" in profile["summary"]
            assert set(f["source_metric_keys"]) <= source_keys
        if details["anchor"] is not None:
            f = details["anchor"]
            assert f["role"] == "group_definition" and not f["new_agent_fact"]
            assert next(
                d for d in c.dimensions if d.dimension.name == f["dimension"]
            ).condition_defined


def test_group_definition_is_not_promoted_to_extra_finding(runs):
    profile = runs["five"][0]["profiles"][0]
    assert profile["explanation"]["anchor"]["dimension"] == "ALGORITHMS"
    assert [f["dimension"] for f in profile["explanation"]["highlights"]] == ["IMPLEMENTATION"]
    assert "代码实现" in profile["why_show"] and "算法" not in profile["why_show"]
    assert "未用于分组" in profile["why_show"]
    assert "26人共同满足" in profile["summary"]
    assert "5.00分" in profile["summary"] and "1.00分" in profile["summary"]
    assert "高4.00分" in profile["summary"]


def test_balanced_cohort_has_opposite_meaningful_explanations(runs):
    profiles = runs[RECIPES[0]][0]["profiles"]
    assert len(profiles) == 2 and len({p["title"] for p in profiles}) == 2
    assert {p["explanation"]["highlights"][0]["direction"] for p in profiles} == {-1, 1}
    for p in profiles:
        assert "24人共同满足" in p["summary"] and "本次72人" in p["summary"]
        assert "1.50分" in p["summary"]


def test_overlapping_groups_emphasize_new_direction_without_changing_members(runs):
    result, book, _ = runs[RECIPES[1]]
    assert [p["metrics"]["target_count"] for p in result["profiles"]] == [48, 48, 32]
    third = result["profiles"][2]
    assert third["explanation"]["overlaps_previous"]
    assert [f["dimension"] for f in third["explanation"]["highlights"]] == ["IMPLEMENTATION"]
    assert set(third["explanation"]["omitted_repeated_dimensions"]) == {"MODELING", "EFFICIENCY"}
    assert "代码实现均分2.75分，其余3.50分，低0.75分" in third["summary"]
    assert "人数不能相加" in third["summary"]
    assert "解题策略评分相对较高" in third["title"]
    assert len({p["title"] for p in result["profiles"]}) == 3
    assert book.verify(third["profile_id"]).discovery.payload.selection.scope.student_count == 96


def test_explanations_make_no_unsupported_student_or_teaching_claims(runs):
    for result, _, _ in runs.values():
        for p in result["profiles"]:
            positive_text = " ".join(
                p[k]
                for k in ("title", "summary", "common_pattern", "why_show", "suggested_next_action")
            )
            for phrase in (
                "心理",
                "懒惰",
                "粗心",
                "具体代码错误",
                "导致",
                "优秀型",
                "薄弱型",
                "依赖AI",
                "提高成绩",
                "教学效果",
                "已经掌握",
            ):
                assert phrase not in positive_text
            assert "原始评分记录" in p["suggested_next_action"]
            assert "经授权的评价依据" in p["suggested_next_action"]
        text = teacher_markdown(result)
        assert "按规则生成" in text and "真实Qwen未运行" in text
    assert "暂无符合展示条件" in teacher_markdown(runs[RECIPES[2]][0])


def test_all_repeated_directions_are_disclosed_and_not_claimed_as_new(books):
    from prototypes.capability_profiles.explanations import PreviousDescription, description_context

    book = books["five"]
    c = book.candidates[0]
    first = teacher_view(book, c.profile_id)
    second = teacher_view(
        book, c.profile_id, previous=(description_context(c.discovery.payload, first),)
    )
    assert second["explanation"]["only_repeated_directions"]
    assert "不重复作为新增调查结论" in second["summary"]
    assert "条件不同" not in second["why_show"]
    assert second["dimensions"] == first["dimensions"] and second["metrics"] == first["metrics"]
    context = description_context(c.discovery.payload, first)
    p = c.discovery.payload
    weak = PreviousDescription(
        frozenset((*p.selection.matches[:1], *p.remainder[:19])), context.features
    )
    separate = teacher_view(book, c.profile_id, previous=(weak,))
    assert separate["explanation"]["overlaps_previous"]
    assert not separate["explanation"]["substantial_overlap_for_narrative_dedup"]
    assert not separate["explanation"]["only_repeated_directions"]
    assert "人数不能相加" in separate["summary"]


def test_neutral_fallback_requires_no_fabricated_difference():
    from prototypes.capability_profiles.engine import POLICY, dataset_from_source
    from prototypes.capability_profiles.explanations import explain

    from app.analysis.edu.discovery import compare_groups
    from app.analysis.edu.models import Condition, Dimension, Operator

    dataset, kernel = dataset_from_source(admit("constant17"))
    comparison = compare_groups(
        dataset,
        batch_id=kernel.data.batch.batch_id,
        conditions=(Condition(Dimension.ALGORITHMS, Operator.LE, 3),),
    )
    content = explain(comparison, POLICY, "算法与数据结构≤3分")
    assert content["title"] == "本次测评的一组参评者"
    assert content["explanation"]["highlights"] == []
    assert "暂缺足够有效" in content["why_show"]
    assert "均分" not in content["summary"]


def test_before_after_comparator_accepts_text_change_but_rejects_fact_change(runs, tmp_path):
    from prototypes.capability_profiles.readability_demo import compare

    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    for name, (result, _, _) in runs.items():
        (before / (name + ".technical.json")).write_text(json.dumps(result), encoding="utf-8")
        edited = json.loads(json.dumps(result))
        for profile in edited["profiles"]:
            profile["title"] = "only text changed"
        (after / (name + ".technical.json")).write_text(json.dumps(edited), encoding="utf-8")
    assert len(compare(before, after)) == 4
    path = after / "five.technical.json"
    edited = json.loads(path.read_text(encoding="utf-8"))
    edited["profiles"][0]["metrics"]["target_count"] += 1
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(ValueError, match="profile_facts_changed"):
        compare(before, after)

    path.write_text((before / "five.technical.json").read_text(encoding="utf-8"), encoding="utf-8")
    empty_path = after / (RECIPES[2] + ".technical.json")
    empty = json.loads(empty_path.read_text(encoding="utf-8"))
    empty["verified_observations"][0]["values"]["items"][0]["mean"]["numerator"] += 1
    empty_path.write_text(json.dumps(empty), encoding="utf-8")
    with pytest.raises(ValueError, match="verified_tool_observations_changed"):
        compare(before, after)
