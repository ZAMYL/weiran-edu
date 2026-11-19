"""Explicit local-only DS-02 replay. Invoke this file with pytest --tb=no."""

import json
import os
from collections import Counter
from pathlib import Path

import pytest
from test_c01_joint_acceptance import (
    RAW_FAILURES,
    make_source,
    trace,
    verify_card,
    verify_witnesses,
)

from scripts.c01_joint_acceptance import test_status as observed_status


def test_local_redacted_zip(tmp_path: Path):
    archive = os.environ.get("EDU_C01_REDACTED_ZIP")
    if not archive:
        pytest.fail("Set EDU_C01_REDACTED_ZIP to an authorized local archive", pytrace=False)
    from ds02_audit import independent

    path = Path(archive)
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
        assert observed_status(t) == RAW_FAILURES.get(original[1], "unknown")
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
