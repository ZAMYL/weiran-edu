"""Acceptance of the three locked PRs through real ZIP bytes and offline Agent tools."""

import asyncio
import json
import os
from collections import Counter
from dataclasses import replace
from fractions import Fraction
from html import escape
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import httpx
import pytest
from prototypes.adaptive_assessment.live.budget import CampaignBudget, OfflineProgrammingPermit
from prototypes.adaptive_assessment.live.demo import MOCK_DATE, native_response
from prototypes.adaptive_assessment.live.pricing import PRICE
from prototypes.research_agent_v2.programming import (
    INSPECT,
    OVERVIEW,
    ProgrammingExecutor,
    investigate_programming,
)
from prototypes.research_agent_v2.programming_evidence import (
    EvidenceScope,
    ProgrammingSource,
)

from app.ai.edu.discovery.models import AgentError, Limits
from app.analysis.code import (
    Language,
    SourceRef,
    SubmissionRecord,
    Verdict,
    analyze,
    b01_test_point_result,
)
from app.analysis.code.features import FeatureId, is_present
from app.analysis.code.interop import (
    c01_feature_associations,
    c01_feature_code,
    c01_test_point_status,
)
from app.importing.edu.answersheet import MAX_CODE_CHARS
from scripts.c01_joint_acceptance import (
    LocalAcceptanceProvider,
    _without_test_points,
    digest,
    materialize,
)
from scripts.c01_joint_acceptance import (
    test_point as observed_test_point,
)

RAW_FAILURES = {
    "答案错误": "wrong_answer",
    "运行超时": "timeout",
    "内存超限": "memory_limit",
    "运行错误": "runtime_error",
    "运行时错误": "runtime_error",
    "段错误": "runtime_error",
    "非零返回": "runtime_error",
    "格式错误": "format_error",
    "答案正确": "accepted",
}


def package(*, changed: bool = False, empty_coverage: bool = False) -> bytes:
    problem = "算法A-2" if not changed else "编程B-7"
    other = "编译C" if not changed else "编译D-88"
    indexed = "items = [2, 3]\nprint(items[0])\n"
    out = BytesIO()
    fields = ["测试点", "结果", "测试点得分", "耗时", "内存"]
    with ZipFile(out, "w") as zip_file:
        for i in range(40):
            valid = i < 35 and not empty_coverage
            status = "答案错误" if i < 25 else "答案正确"
            verdict = "部分正确" if i < 5 else status
            lang = "GCC" if 10 <= i < 15 else "PYTHON3"
            code = indexed if i < 5 else "print(2)\n"
            if 15 <= i < 20:
                code += "#" * MAX_CODE_CHARS
            pre = f"<pre>{escape(code)}</pre>" if not 20 <= i < 25 else ""
            cells = ["900", status if valid else "待复核", "0", "12.50 ms", "304 KB"]
            pairs = list(zip(fields, cells, strict=True))
            if changed:
                pairs.reverse()
            heads = "".join(f"<th>{k}</th>" for k, _ in pairs)
            values = "".join(f"<td>{v}</td>" for _, v in pairs)
            # Valid records from another point must not inflate coverage.
            second = "".join(
                f"<td>{v}</td>"
                for k, v in (
                    (k, {"测试点": "901", "结果": "答案正确", "测试点得分": "5"}.get(k, v))
                    for k, v in pairs
                )
            )
            table = (
                f"<table><thead><tr>{heads}</tr></thead>"
                f"<tbody><tr>{values}</tr><tr>{second}</tr></tbody></table>"
            )
            compile_verdict = "编译错误" if changed and i < 15 else "未作答"
            compile_code = "" if compile_verdict == "编译错误" else "<pre>无</pre>"

            def block(label: str, body: str, outcome: str) -> str:
                return (
                    "<div class='flex space-x-2'>"
                    f"<div class='flex-none font-medium'>{label}</div>"
                    f"<div class='flex-1 space-y-4'>{body}</div>"
                    f"<div class='flex-none min-w-[5rem]'><div>{outcome}</div>"
                    "<span>0 / 5</span></div></div>"
                )

            zip_file.writestr(
                f"合成包/正式答卷/synthetic-{i:02}.html",
                "<html><body><div class='box'>"
                f"<div><span>考生</span><span>SYN{i:02} 合成测试</span></div></div>"
                "<div class='space-y-8'>"
                + block(problem, f"<div>编译器: {lang}</div>{pre}{table}", verdict)
                + block(other, compile_code, compile_verdict)
                + "</div></body></html>",
            )
    return out.getvalue()


def raw_synthetic_blocks(blob: bytes) -> dict:
    """Independent XML read of fixture HTML, including exact source locations."""
    result = {}
    with ZipFile(BytesIO(blob)) as zipped:
        for entry in zipped.namelist():
            doc = ElementTree.fromstring(zipped.read(entry))
            participant = doc.findall(".//span")[1].text.split()[0]
            for index, block in enumerate(doc.findall(".//div[@class='flex space-x-2']")):
                header, code, verdict = block
                pre = code.find("pre")
                fields = [h.text for h in code.findall(".//thead/tr/th")]
                tests = [
                    dict(zip(fields, [v.text for v in row], strict=True))
                    for row in code.findall(".//tbody/tr")
                ]
                result[entry, index] = (
                    participant,
                    header.text,
                    verdict[0].text,
                    None if pre is None else pre.text,
                    tests,
                )
    return result


def trace(source: ProgrammingSource, directory: Path, *, private_terms: tuple[str, ...] = ()):
    decisions = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        wire = json.dumps(payload, ensure_ascii=False)
        if any(term in wire for term in private_terms):
            raise AgentError("private_source_in_model_payload")
        facts = json.loads(payload["messages"][-1]["content"])["facts"]
        if not facts:
            decision = {"name": OVERVIEW, "arguments": {}}
        elif len(facts) == 1 and facts[0]["directions"]:
            overview = facts[0]
            strongest = max(
                overview["directions"],
                key=lambda direction: Fraction(direction["affected"], direction["covered"]),
            )
            decision = {
                "name": INSPECT,
                "arguments": {
                    "overview_id": overview["fact_id"],
                    "direction": strongest["direction"],
                },
                "basis_fact_ids": [overview["fact_id"]],
            }
        else:
            ids = [fact["fact_id"] for fact in facts if fact["qualified"]]
            decision = {
                "name": "finish",
                "arguments": {
                    "selected_fact_ids": ids,
                    "reason": "completed" if ids else "insufficient_data",
                },
            }
        decisions.append(decision)
        return httpx.Response(200, json=native_response(decision, len(decisions)))

    permit = OfflineProgrammingPermit(source, "joint-acceptance", PRICE.fingerprint)
    budget = CampaignBudget(permit, directory, origin="http_mock", today=MOCK_DATE)
    try:
        run, executor = asyncio.run(
            investigate_programming(
                source,
                "调查编程错误模式",
                budget,
                transport=httpx.MockTransport(respond),
            )
        )
        assert budget.snapshot()["request_count"] == len(decisions)
    finally:
        budget.close()
    return run, executor, decisions


def verify_witnesses(accepted, blob: bytes) -> None:
    """Bind all generated references back to parsed upstream rows and archive bytes."""
    witnesses = {w.reference_id: w for w in accepted.snapshot.witnesses}
    measured = {
        (i.reference.student_id, a.problem_id): i.features
        for a in accepted.analyses
        for i in a.submissions
    }
    parsed = {(s.participant_key, s.problem_label): s for s in accepted.parsed.submissions}
    tests = {
        (t.participant_key, t.problem_label, str(t.test_index)): t
        for t in accepted.parsed.observations
    }
    feature_ids = {c01_feature_code(f): f for f in FeatureId}
    with ZipFile(BytesIO(blob)) as zipped:
        entries = {s.source_entry: digest(zipped.read(s.source_entry)) for s in parsed.values()}
    for s in accepted.snapshot.submissions:
        subject = s.participant, s.problem
        row = parsed[subject]
        anchor = f"{row.source_entry}#block/{row.source_index}"

        def check(
            ref: str,
            kind: str,
            suffix: str,
            original: str,
            subject=subject,
            source_entry=row.source_entry,
            anchor=anchor,
        ) -> None:
            w = witnesses[ref]
            assert (w.participant_id, w.item_id) == subject
            assert w.kind == kind and w.source_sha256 == accepted.snapshot.snapshot
            assert w.entry_sha256 == entries[source_entry]
            assert w.locator == anchor + suffix
            assert w.content_sha256 == digest(original.encode())

        check(s.verdict_ref, "verdict", "/verdict", row.verdict or "")
        if s.code_ref:
            assert row.code_state == "PRESENT" and not row.code_truncated
            check(s.code_ref, "code", "/code", row.code)
        for test in s.tests:
            t = tests[*subject, test.point]
            assert test.status == RAW_FAILURES.get(t.status, "unknown")
            check(
                test.ref,
                "test",
                f"/result/{t.source_row}",
                json.dumps(
                    [test.point, t.status, t.score, t.duration_text, t.memory_text],
                    ensure_ascii=False,
                ),
            )
        for feature in accepted.snapshot.features:
            if feature.problem != s.problem:
                continue
            f = feature_ids[feature.code]
            measurements = measured[subject]
            refs = dict(feature.present)
            if s.participant not in feature.checked:
                assert s.participant not in refs
                continue
            if s.participant in refs:
                mark = measurements.get(f)
                check(
                    refs[s.participant],
                    "feature",
                    f"/code/feature/{feature.code}",
                    json.dumps(
                        [
                            witnesses[s.code_ref].content_sha256,
                            feature.code,
                            mark.value,
                            mark.confidence.value,
                        ],
                    ),
                )
            assert (s.participant in refs) == is_present(measurements, f)


def verify_card(run, executor, accepted) -> dict:
    assert (run.status, run.model_requests, run.tool_attempts) == ("completed", 3, 2)
    assert [step.tool for step in run.tools] == [OVERVIEW, INSPECT]
    (card,) = run.cards
    observed = card["direction"]
    metric = observed["direction"]
    detail = run.facts[1]
    assert detail.view["basis_fact_id"] == run.facts[0].fact_id
    resolved = {
        q["metric_key"]: executor.resolve(q["fact_id"], q["metric_key"])
        for q in card["source_queries"]
    }
    rows = [s for s in accepted.snapshot.submissions if s.problem == card["problem"]]
    test_point = card["test_point"]
    if test_point is not None:
        relevant = [t for s in rows for t in s.tests if t.point == test_point]
        assert observed["denominator"] == "observed_test_verdicts"
        expected_cover = {t.ref for t in relevant if t.status != "unknown"}
        expected_affected = {t.ref for t in relevant if t.status == observed["phenomenon"]}
    else:
        assert observed["denominator"] == "known_attempts"
        expected_cover = {
            s.verdict_ref for s in rows if s.verdict not in {"unknown", "not_attempted"}
        }
        expected_affected = {
            s.verdict_ref for s in rows if not s.tests and s.verdict == observed["phenomenon"]
        }
    assert {w.reference_id for w in resolved[f"{metric}.coverage"]} == expected_cover
    assert {w.reference_id for w in resolved[f"{metric}.affected"]} == expected_affected
    assert len(expected_cover) == observed["covered"]
    assert len(expected_affected) == observed["affected"]
    return {
        "direction": observed,
        "features": card["features"],
        "limitations": card["limitations"],
        "affected_with_code": card["affected_with_code"],
        "resolved": {key: len(items) for key, items in resolved.items()},
    }


def make_source(blob: bytes):
    accepted = materialize(blob, "local-batch")
    scope = EvidenceScope("local-teacher", "local-task", "local-batch", accepted.snapshot.snapshot)
    provider = LocalAcceptanceProvider(scope, accepted.snapshot, lambda: blob)
    return accepted, ProgrammingSource(scope, provider), provider


def test_point_none_fallback_excludes_test_point_submissions() -> None:
    loop = "for i in range(3):\n    print(i)\n"

    def record(sid: str, tests=()) -> SubmissionRecord:
        return SubmissionRecord(
            "b",
            "p",
            sid,
            f"student-{sid}",
            Language.PYTHON,
            loop,
            Verdict.WRONG_ANSWER,
            tuple(tests),
            SourceRef("src", sid),
        )

    rows = [
        record("A"),  # no test points -> the C-01 fallback participant
        record("B", (b01_test_point_result("900", "答案错误"),)),
        record("C", (b01_test_point_result("900", "答案错误"),)),
    ]
    analysis = analyze("b", "p", rows)

    fallback = c01_feature_associations(_without_test_points(analysis), status="wrong_answer")
    loop_feature = next(f for f in fallback if f.code == "loop")
    assert loop_feature.checked == ("student-A",)
    assert loop_feature.present == (("student-A", "A"),)

    whole = c01_feature_associations(analysis, status="wrong_answer")
    whole_loop = next(f for f in whole if f.code == "loop")
    assert set(whole_loop.checked) == {"student-A", "student-B", "student-C"}


def test_zip_distribution_changes_investigation_and_sources(tmp_path: Path):
    traces = []
    for changed in [False, True]:
        blob = package(changed=changed)
        accepted, source, provider = make_source(blob)
        assert (len(accepted.parsed.students), len(accepted.analyses)) == (40, 2)
        raw = raw_synthetic_blocks(blob)
        parsed_by_subject = {}
        for s in accepted.parsed.submissions:
            pid, problem, verdict, code, _ = raw[s.source_entry, s.source_index]
            assert (s.participant_key, s.problem_label, s.verdict) == (pid, problem, verdict)
            parsed_by_subject[pid, problem] = s
            if s.code_state == "PRESENT":
                assert s.code == code[:MAX_CODE_CHARS]
        for point in accepted.parsed.observations:
            submission = parsed_by_subject[point.participant_key, point.problem_label]
            tests = raw[submission.source_entry, submission.source_index][4]
            original = tests[point.source_row]
            assert (str(point.test_index), point.status, str(point.score)) == (
                original["测试点"],
                original["结果"],
                original["测试点得分"],
            )
            assert point.duration_text == original["耗时"] and point.duration_ms == 12.5
            assert point.memory_text == original["内存"] and point.memory_kb == 304
        verify_witnesses(accepted, blob)
        run, executor, decisions = trace(
            source,
            tmp_path / str(changed),
            private_terms=(
                "synthetic-00.html",
                "SYN00",
                "print(items[0])",
                *source.material.problems,
            ),
        )
        result = verify_card(run, executor, accepted)
        traces.append((decisions[1]["arguments"]["direction"], result["direction"]))
        print(
            json.dumps(
                {"mode": "synthetic", "changed": changed, "decisions": decisions, **result},
                ensure_ascii=False,
            )
        )
        if changed:
            assert result["direction"]["phenomenon"] == "compile_error"
            assert result["direction"]["affected"] == result["direction"]["covered"] == 15
            assert result["features"] == []
            assert "test_evidence_missing" in result["limitations"]
            assert "features_unavailable_or_withheld" in result["limitations"]
            continue

        observed = result["direction"]
        assert (observed["affected"], observed["covered"]) == (25, 35)
        assert result["affected_with_code"] == 15
        assert {"feature": "indexing", "checked": 10, "present": 5} in result["features"]
        a = accepted.analyses[0]
        assert {s.status.value: s.count for s in a.source_statuses} == {
            "ok": 25,
            "truncated": 5,
            "unknown_language": 5,
            "missing_source": 5,
        }
        index = next(x for x in a.associations if x.feature is FeatureId.INDEX_ACCESSES)
        assert index.excluded_count == 15
        # The five partial verdicts lie outside the whole-submission 2x2 population.
        assert index.contingency.failed_total + index.contingency.passed_total == 20
        projected = c01_feature_associations(a, point="900", status="wrong_answer")
        indexing = next(f for f in projected if f.code == "indexing")
        checked = executor.resolve(run.facts[1].fact_id, "indexing.checked")
        present = executor.resolve(run.facts[1].fact_id, "indexing.present")
        assert len(checked) == len(indexing.checked) == 10
        assert len(present) == len(indexing.present) == 5
        assert {w.participant_id for w in checked} == set(indexing.checked)
        assert {w.participant_id for w in present} == {p for p, _ in indexing.present}
        assert {w.kind for w in checked} == {"code"}
        assert {w.kind for w in present} == {"feature"}
        fact = run.facts[1].fact_id
        for key in ("actor", "batch", "task", "snapshot"):
            with pytest.raises(AgentError, match="access_denied"):
                ProgrammingSource(replace(source.scope, **{key: "wrong"}), provider)
        other = ProgrammingExecutor(source, "separate-run", Limits(), source.material.problems)
        with pytest.raises(AgentError, match="unknown_fact_id"):
            other.resolve(fact, "indexing.present")
        newer_zip = BytesIO(blob)
        with ZipFile(newer_zip, "a") as archive:
            archive.comment = b"new snapshot, same participants and observations"
        newer_blob = newer_zip.getvalue()
        newer = materialize(newer_blob, source.scope.batch)
        newer_scope = replace(source.scope, task="next-task", snapshot=newer.snapshot.snapshot)
        newer_provider = LocalAcceptanceProvider(
            newer_scope, newer.snapshot, lambda newer_blob=newer_blob: newer_blob
        )
        newer_source = ProgrammingSource(newer_scope, newer_provider)
        newer_executor = ProgrammingExecutor(
            newer_source, "next-run", Limits(), newer_source.material.problems
        )
        with pytest.raises(AgentError, match="unknown_fact_id"):
            newer_executor.resolve(fact, "indexing.present")
        assert present[0].reference_id not in {
            w.reference_id for w in newer_source.material.witnesses
        }
        altered_features = tuple(
            replace(
                item, present=((item.present[0][0], present[0].reference_id), *item.present[1:])
            )
            if item.point == "900" and item.status == "wrong_answer" and item.code == "indexing"
            else item
            for item in newer.snapshot.features
        )
        newer_provider.snapshot = replace(newer.snapshot, features=altered_features)
        altered_source = ProgrammingSource(newer_scope, newer_provider)
        altered_executor = ProgrammingExecutor(
            altered_source, "altered-run", Limits(), altered_source.material.problems
        )
        (overview,) = altered_executor.execute(OVERVIEW, altered_executor.parse(OVERVIEW, {}))
        with pytest.raises(AgentError, match="source_scope_mismatch"):
            altered_executor.execute(
                INSPECT,
                altered_executor.parse(
                    INSPECT, {"overview_id": overview.fact_id, "direction": "D1"}
                ),
            )
        original_source = provider.read_bytes
        provider.read_bytes = lambda blob=blob: blob + b"changed"
        with pytest.raises(AgentError, match="source_validation_failed"):
            executor.resolve(fact, "indexing.present")
        provider.read_bytes = original_source
        original_scope = provider.scope
        provider.scope = replace(original_scope, task="replacement-task")
        with pytest.raises(AgentError, match="access_denied"):
            executor.resolve(fact, "indexing.present")
        provider.scope = original_scope
        provider.details_allowed = False
        with pytest.raises(AgentError, match="access_denied"):
            executor.resolve(fact, "indexing.checked")
        provider.details_allowed = True
        original_snapshot = provider.snapshot
        provider.snapshot = replace(original_snapshot, witnesses=original_snapshot.witnesses[:-1])
        with pytest.raises(AgentError, match="source_validation_failed"):
            executor.resolve(fact, "D1.coverage")
        provider.snapshot = original_snapshot
        provider.allowed = False
        with pytest.raises(AgentError, match="access_denied"):
            executor.verify_all()
    assert [alias for alias, _ in traces] == ["D1", "D2"]
    assert [direction["problem"] for _, direction in traces] == ["P1", "P2"]


def test_unknown_coverage_and_missing_witness_fail_closed(tmp_path: Path):
    accepted, source, provider = make_source(package(empty_coverage=True))
    assert (
        sum(
            t.status != "unknown" and t.point == "900"
            for submission in accepted.snapshot.submissions
            for t in submission.tests
        )
        == 0
    )
    run, _, decisions = trace(source, tmp_path)
    assert run.status == "completed" and run.reason == "insufficient_data"
    assert not run.cards and not run.facts[0].view["directions"]
    assert [step["name"] for step in decisions] == [OVERVIEW, "finish"]
    print("unknown-only target coverage=0, insufficient_data, no card")
    missing = accepted.snapshot.submissions[0].verdict_ref
    provider.snapshot = replace(
        provider.snapshot,
        witnesses=tuple(w for w in provider.snapshot.witnesses if w.reference_id != missing),
    )
    with pytest.raises(AgentError, match="source_scope_mismatch"):
        ProgrammingExecutor(
            ProgrammingSource(source.scope, provider),
            "missing-source",
            Limits(),
            source.material.problems,
        )


def verify_local_redacted_zip(path: Path, tmp_path: Path) -> None:
    from ds02_audit import independent

    blob = path.read_bytes()
    accepted, source, provider = make_source(blob)
    provider.read_bytes = path.read_bytes
    ds02 = independent(blob)
    student_files = {
        entry: (header, blocks) for role, entry, header, blocks, _ in ds02 if role == "正式答卷"
    }
    parsed = accepted.parsed
    expected_students = {header["考生"].split()[0] for header, _ in student_files.values()}
    assert set(accepted.snapshot.participants) == expected_students
    assert len(parsed.submissions) == sum(len(blocks) for _, blocks in student_files.values())
    assert len(parsed.observations) == sum(
        len(b["rows"]) for _, blocks in student_files.values() for b in blocks
    )
    by_subject = {(s.participant_key, s.problem_label): s for s in parsed.submissions}
    for s in parsed.submissions:
        header, blocks = student_files[s.source_entry]
        b = blocks[s.source_index]
        if not (
            s.participant_key == header["考生"].split()[0]
            and s.problem_label == b["label"]
            and s.verdict == b["verdict"]
            and s.language == b["language"]
            and s.score == (int(b["score"]) if (b["score"] or "").isdigit() else None)
            and s.score_total == int(b["total"])
            and (s.code_state != "PRESENT" or s.code == b["code"])
            and (s.compiler_output or "") == ("" if b["diag"] == "无" else b["diag"])
        ):
            pytest.fail("DS-02 submission/source field mismatch", pytrace=False)
    for t in parsed.observations:
        s = by_subject[t.participant_key, t.problem_label]
        original = student_files[s.source_entry][1][s.source_index]["rows"][t.source_row]
        if (str(t.test_index), t.status, str(t.score), t.duration_text, t.memory_text) != original:
            pytest.fail("DS-02 test row source or field mismatch", pytrace=False)
        assert c01_test_point_status(observed_test_point(t)) == RAW_FAILURES.get(
            original[1], "unknown"
        )
    verify_witnesses(accepted, blob)
    terms = tuple(p for p in expected_students if len(p) >= 8)
    run, executor, decisions = trace(
        source,
        tmp_path,
        private_terms=(*terms, *student_files),
    )
    result = verify_card(run, executor, accepted)
    print(
        json.dumps(
            {
                "mode": "local_redacted",
                "files": len(ds02),
                "participants": len(parsed.students),
                "problems": len(parsed.problems),
                "submissions": len(parsed.submissions),
                "observations": len(parsed.observations),
                "states": Counter(s.code_state for s in parsed.submissions),
                "feature_parse": Counter(
                    i.features.status.value for a in accepted.analyses for i in a.submissions
                ),
                "witnesses": Counter(w.kind for w in accepted.snapshot.witnesses),
                "decisions": decisions,
                **result,
            },
            ensure_ascii=False,
        )
    )


_local_archive = os.environ.get("EDU_C01_REDACTED_ZIP")
if _local_archive:
    # The private acceptance is opt-in. The Windows shard verifier requires
    # every collected CI test to run through setup, call and teardown.
    def test_local_redacted_zip(tmp_path: Path) -> None:
        verify_local_redacted_zip(Path(_local_archive), tmp_path)
