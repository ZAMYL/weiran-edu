from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from app.core.errors import APIError
from app.edu.models import ImportPatch

from .conftest import commit


def expect(code, action, status=None):
    with pytest.raises(APIError) as error:
        action()
    assert error.value.code == code
    if status:
        assert error.value.status_code == status


def count(runtime, table):
    # Only fixed test-owned table names are supplied.
    with runtime.db.transaction() as connection:
        return connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]


def test_preview_does_not_create_formal_records_and_commit_survives_new_service(runtime, stage):
    view = stage()
    assert count(runtime, "imports") == 1
    for table in ("students", "student_identities", "batches", "evaluations"):
        assert count(runtime, table) == 0
    result = commit(runtime, view)
    assert result.result == "created"
    assert count(runtime, "students") == 2 and count(runtime, "evaluations") == 10
    assert count(runtime, "rubric_criteria") == 5
    assert count(runtime, "import_payloads") == 0 and count(runtime, "batch_sources") == 1
    from app.edu.service import EduService

    restarted = EduService(runtime.db)
    assert restarted.get_batch(runtime.p, result.batch_id).evaluation_count == 10
    final = restarted.get_import(runtime.p, view.import_info.import_id)
    assert not final.preview_available and final.rows is None and final.student_samples is None


def test_replay_and_duplicate_leave_original_batch_metadata(runtime, stage):
    first = stage(title="首次标题")
    created = commit(runtime, first)
    assert commit(runtime, first).result == "replayed"
    duplicate = commit(runtime, stage(title="不能覆盖原标题"))
    assert duplicate.result == "duplicate" and duplicate.batch_id == created.batch_id
    assert runtime.service.get_batch(runtime.p, created.batch_id).title == "首次标题"
    assert count(runtime, "batches") == 1 and count(runtime, "evaluations") == 10


def test_semantic_duplicate_ignores_style(runtime, stage, book):
    original = commit(runtime, stage())
    book["Sheet2"]["C3"].number_format = "0.00"
    duplicate = commit(runtime, stage())
    assert duplicate.result == "duplicate" and duplicate.batch_id == original.batch_id


def test_invalid_preview_never_commits(runtime, stage, book):
    book["Sheet2"]["C3"] = None
    view = stage()
    assert view.import_info.status == "invalid"
    expect("IMPORT_NOT_CONFIRMABLE", lambda: commit(runtime, view), 409)
    assert count(runtime, "batches") == count(runtime, "students") == 0


def test_warning_requires_explicit_acknowledgement(runtime, stage, book):
    book["Sheet2"]["D3"] = None
    view = stage()
    expect("WARNINGS_NOT_ACKNOWLEDGED", lambda: commit(runtime, view))
    assert commit(runtime, view, acknowledge=True).result == "created"


def test_patch_invalidates_old_version_and_does_not_extend_expiry(runtime, stage):
    view = stage()
    updated = runtime.service.patch_import(
        runtime.p,
        view.import_info.import_id,
        ImportPatch(
            preview_version=view.import_info.preview_version,
            title="新标题",
            course="合成课程",
            assessment_date="2026-10-24",
        ),
    )
    assert updated.import_info.expires_at == view.import_info.expires_at
    assert updated.rows == view.rows and updated.issues == view.issues
    expect("PREVIEW_VERSION_MISMATCH", lambda: commit(runtime, view))
    result = commit(runtime, updated)
    assert runtime.service.get_batch(runtime.p, result.batch_id).course == "合成课程"
    expect(
        "IMPORT_NOT_CONFIRMABLE",
        lambda: runtime.service.patch_import(
            runtime.p,
            view.import_info.import_id,
            ImportPatch(preview_version=updated.import_info.preview_version, course=None),
        ),
    )


def test_expiry_and_cleanup_do_not_break_committed_replay(runtime, stage):
    confirmed = stage()
    commit(runtime, confirmed)
    pending = stage()
    runtime.clock[0] += timedelta(hours=25)
    expect(
        "IMPORT_EXPIRED",
        lambda: runtime.service.get_import(runtime.p, pending.import_info.import_id),
        410,
    )
    expect("IMPORT_EXPIRED", lambda: commit(runtime, pending), 410)
    assert runtime.service.cleanup_expired() == 1
    assert count(runtime, "import_payloads") == 0
    assert commit(runtime, confirmed).result == "replayed"
    expect(
        "IMPORT_NOT_CONFIRMABLE",
        lambda: runtime.service.patch_import(
            runtime.p,
            confirmed.import_info.import_id,
            ImportPatch(preview_version=confirmed.import_info.preview_version, title="不能修改"),
        ),
    )


def test_revisions_preserve_student_ids_old_evidence_and_current_filter(runtime, stage, book):
    first = commit(runtime, stage())
    old_students = runtime.reader.students(runtime.p, first.batch_id)
    old_evidence = old_students.items[0].scores[0].evidence_id
    book["Sheet2"]["C3"] = 4.5
    pending_a = stage(revision=first.batch_id)
    book["Sheet2"]["C3"] = 3.5
    pending_b = stage(revision=first.batch_id)
    new = commit(runtime, pending_a)
    expect("REVISION_CONFLICT", lambda: commit(runtime, pending_b))
    assert runtime.service.list_batches(runtime.p).total_count == 1
    assert runtime.service.list_batches(runtime.p, include_revisions=True).total_count == 2
    assert not runtime.service.get_batch(runtime.p, first.batch_id).is_current
    assert (
        runtime.reader.students(runtime.p, new.batch_id).items[0].student_id
        == old_students.items[0].student_id
    )
    assert runtime.reader.evidence(runtime.p, first.batch_id, old_evidence).score == 2.75
    assert runtime.reader.students(runtime.p, new.batch_id).items[0].scores[0].score == 4.5
    expect(
        "RESOURCE_NOT_FOUND",
        lambda: runtime.reader.evidence(runtime.p, new.batch_id, old_evidence),
        404,
    )


def test_identity_conflict_is_blocked_before_semantic_dedup(runtime, stage, book):
    commit(runtime, stage())
    book["Sheet2"]["A2"] = "000000000001 私密变化姓名"
    view = stage()
    assert view.import_info.status == "invalid"
    assert any(i.code == "STUDENT_IDENTITY_CONFLICT" for i in view.issues)
    assert "私密变化姓名" not in view.model_dump_json()
    expect("IMPORT_NOT_CONFIRMABLE", lambda: commit(runtime, view))
    assert count(runtime, "batches") == 1


def test_identity_conflict_created_after_preview_is_rechecked_in_transaction(runtime, stage, book):
    first = stage()
    book["Sheet2"]["A2"] = "000000000001 私密并发姓名"
    other = stage()
    commit(runtime, first)
    expect("STUDENT_IDENTITY_CONFLICT", lambda: commit(runtime, other))
    assert count(runtime, "batches") == 1


def test_concurrent_commits_of_one_preview_are_idempotent(runtime, stage):
    view = stage()
    parser = runtime.service.parser
    barrier = Barrier(2)

    def synchronized(data):
        barrier.wait(timeout=5)
        return parser(data)

    runtime.service.parser = synchronized
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: commit(runtime, view), range(2)))
    assert {r.result for r in results} == {"created", "replayed"}
    assert len({r.batch_id for r in results}) == count(runtime, "batches") == 1


def test_concurrent_duplicate_previews_create_one_batch(runtime, stage):
    views = [stage(), stage()]
    parser, barrier = runtime.service.parser, Barrier(2)

    def synchronized(data):
        barrier.wait(timeout=5)
        return parser(data)

    runtime.service.parser = synchronized
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda view: commit(runtime, view), views))
    assert {r.result for r in results} == {"created", "duplicate"}
    assert count(runtime, "batches") == 1 and count(runtime, "evaluations") == 10


def test_mid_write_failure_rolls_back_all_formal_records_and_can_retry(runtime, stage, monkeypatch):
    view = stage()
    original = runtime.service._persist_evaluation
    calls = [0]

    def fail_second(connection, values):
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("synthetic write fault")
        return original(connection, values)

    monkeypatch.setattr(runtime.service, "_persist_evaluation", fail_second)
    with pytest.raises(RuntimeError, match="synthetic write fault"):
        commit(runtime, view)
    for table in (
        "batches",
        "evaluations",
        "students",
        "student_identities",
        "rubric_snapshots",
        "rubric_criteria",
        "batch_sources",
    ):
        assert count(runtime, table) == 0
    assert (
        runtime.service.get_import(runtime.p, view.import_info.import_id).import_info.status
        == "preview_ready"
    )
    monkeypatch.setattr(runtime.service, "_persist_evaluation", original)
    assert commit(runtime, view).result == "created"


@pytest.mark.parametrize("change", ["credentials", "permission", "expiry", "version"])
def test_commit_rechecks_changes_that_happen_during_parsing(runtime, stage, change):
    view = stage()
    parser = runtime.service.parser

    def change_while_parsing(data):
        parsed = parser(data)
        if change == "credentials":
            runtime.admin.revoke_tokens(runtime.teacher)
        elif change == "permission":
            runtime.admin.set_member(runtime.teacher, runtime.scope, can_import=False)
        elif change == "expiry":
            runtime.clock[0] += timedelta(hours=25)
        else:
            runtime.service.patch_import(
                runtime.p,
                view.import_info.import_id,
                ImportPatch(
                    preview_version=view.import_info.preview_version,
                    title="并发变化标题",
                ),
            )
        return parsed

    runtime.service.parser = change_while_parsing
    expected = {
        "credentials": "UNAUTHORIZED",
        "permission": "FORBIDDEN",
        "expiry": "IMPORT_EXPIRED",
        "version": "PREVIEW_VERSION_MISMATCH",
    }
    expect(expected[change], lambda: commit(runtime, view))
    assert count(runtime, "batches") == count(runtime, "students") == 0
