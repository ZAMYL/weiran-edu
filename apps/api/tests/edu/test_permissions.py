import hashlib

import pytest

from app.core.errors import APIError
from app.edu.auth import Principal, authenticate

from .conftest import commit
from .test_lifecycle import expect


def test_no_implicit_batch_or_import_sharing_within_scope(runtime, stage):
    view = stage()
    created = commit(runtime, view)
    assert runtime.service.list_batches(runtime.q).total_count == 0
    expect(
        "RESOURCE_NOT_FOUND",
        lambda: runtime.service.get_import(runtime.q, view.import_info.import_id),
        404,
    )
    expect(
        "RESOURCE_NOT_FOUND", lambda: runtime.service.get_batch(runtime.q, created.batch_id), 404
    )
    expect("RESOURCE_NOT_FOUND", lambda: runtime.reader.students(runtime.q, created.batch_id), 404)
    runtime.admin.grant_read(runtime.other, created.batch_id)
    assert runtime.service.get_batch(runtime.q, created.batch_id).batch_id == created.batch_id
    assert runtime.reader.students(runtime.q, created.batch_id).total_count == 2
    expect(
        "RESOURCE_NOT_FOUND",
        lambda: runtime.service.get_import(runtime.q, view.import_info.import_id),
        404,
    )
    runtime.admin.revoke_read(runtime.other, created.batch_id)
    expect("RESOURCE_NOT_FOUND", lambda: runtime.reader.students(runtime.q, created.batch_id), 404)


def test_hidden_duplicate_conflict_does_not_disclose_batch_id(runtime, stage):
    batch = commit(runtime, stage())
    other = stage(principal=runtime.q)
    with pytest.raises(APIError) as error:
        commit(runtime, other, principal=runtime.q)
    assert error.value.code == "IMPORT_CONFLICT"
    assert batch.batch_id not in str(error.value)


def test_revoked_membership_blocks_existing_principal_and_committed_replay(runtime, stage):
    view = stage()
    batch = commit(runtime, view)
    runtime.admin.remove_member(runtime.teacher, runtime.scope)
    expect("RESOURCE_NOT_FOUND", lambda: runtime.service.get_batch(runtime.p, batch.batch_id), 404)
    expect("RESOURCE_NOT_FOUND", lambda: commit(runtime, view), 404)


def test_import_permission_revocation_after_preview_blocks_write(runtime, stage):
    view = stage()
    runtime.admin.set_member(runtime.teacher, runtime.scope, can_import=False)
    expect("FORBIDDEN", lambda: commit(runtime, view), 403)
    assert runtime.service.get_import(runtime.p, view.import_info.import_id).preview_available


@pytest.mark.parametrize("change", ["revoke", "disable", "expire"])
def test_credentials_checked_every_call_without_auth_cache(runtime, stage, change):
    view = stage()
    if change == "revoke":
        runtime.admin.revoke_tokens(runtime.teacher)
    elif change == "disable":
        runtime.admin.set_active(runtime.teacher, False)
    else:
        with runtime.db.transaction(write=True) as connection:
            connection.execute(
                "UPDATE teacher_tokens SET expires_at='2000-01-01T00:00:00.000000Z' "
                "WHERE teacher_id=?",
                (runtime.teacher,),
            )
    expect(
        "UNAUTHORIZED",
        lambda: runtime.service.get_import(runtime.p, view.import_info.import_id),
        401,
    )
    expect("UNAUTHORIZED", lambda: authenticate(runtime.db, runtime.token), 401)


def test_teacher_id_cannot_be_spoofed_and_plain_tokens_are_not_stored(runtime):
    forged = Principal(runtime.other, runtime.p.token_hash)
    expect("UNAUTHORIZED", lambda: runtime.service.list_batches(forged), 401)
    with runtime.db.transaction() as connection:
        row = connection.execute(
            "SELECT token_hash FROM teacher_tokens WHERE teacher_id=?", (runtime.teacher,)
        ).fetchone()
    assert row["token_hash"] == hashlib.sha256(runtime.token.encode()).hexdigest()
    assert runtime.token.encode() not in runtime.db.path.read_bytes()


def test_cross_scope_students_are_distinct_and_read_requires_current_membership(runtime, stage):
    first = commit(runtime, stage())
    scope = runtime.admin.create_scope("另一合成来源")
    runtime.admin.set_member(runtime.teacher, scope, can_import=True)
    second = commit(runtime, stage(scope=scope))
    a = runtime.reader.students(runtime.p, first.batch_id).items[0].student_id
    b = runtime.reader.students(runtime.p, second.batch_id).items[0].student_id
    assert a != b
    expect(
        "RESOURCE_NOT_FOUND",
        lambda: runtime.reader.student_evidence(runtime.p, first.batch_id, b),
        404,
    )


def test_restoring_membership_does_not_restore_revoked_shared_access(runtime, stage):
    batch = commit(runtime, stage())
    runtime.admin.grant_read(runtime.other, batch.batch_id)
    runtime.admin.remove_member(runtime.other, runtime.scope)
    runtime.admin.set_member(runtime.other, runtime.scope, can_import=False)
    expect("RESOURCE_NOT_FOUND", lambda: runtime.service.get_batch(runtime.q, batch.batch_id), 404)


def test_reenabled_teacher_requires_new_credentials(runtime):
    runtime.admin.set_active(runtime.teacher, False)
    runtime.admin.set_active(runtime.teacher, True)
    expect("UNAUTHORIZED", lambda: authenticate(runtime.db, runtime.token), 401)
    assert (
        authenticate(runtime.db, runtime.admin.issue_token(runtime.teacher)).teacher_id
        == runtime.teacher
    )


def test_read_grantee_cannot_revise_owners_batch(runtime, stage, book):
    original = commit(runtime, stage())
    runtime.admin.grant_read(runtime.other, original.batch_id)
    book["Sheet2"]["C3"] = 4
    expect(
        "RESOURCE_NOT_FOUND", lambda: stage(principal=runtime.q, revision=original.batch_id), 404
    )
