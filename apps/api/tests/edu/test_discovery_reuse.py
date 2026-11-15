"""Reuse skips only a completed computation; every request still reads current authority/input."""

import pytest

from app.edu import discovery_worker
from app.edu.discoveries import DiscoveryReader

from .test_insights_api import formal_batch as formal_batch

SESSION = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def executions(monkeypatch):
    calls = []
    execute = discovery_worker.execute

    async def counted(*args, **kwargs):
        calls.append(args)
        return await execute(*args, **kwargs)

    monkeypatch.setattr(discovery_worker, "execute", counted)
    return calls


def query(client, base, batch, *, session=SESSION, suffix="query", **patch):
    return client.post(
        base + "/discoveries/" + suffix,
        headers={"X-EDU-Diagnosis-Session": session} if session else {},
        json={"snapshot_id": batch["snapshot_id"], **patch},
    )


def test_completed_list_detail_and_return_reuse_but_normal_detail_does_not(
    client, formal_batch, executions
):
    base, batch, _ = formal_batch(17)
    first = query(client, base, batch)
    assert first.status_code == 200, first.text
    finding = first.json()["items"][0]["discovery_id"]
    detail = query(client, base, batch, suffix=finding + "/query")
    assert detail.status_code == 200, detail.text
    assert query(client, base, batch).json() == first.json()
    assert len(executions) == 1
    assert query(client, base, batch, session=None, suffix=finding + "/query").status_code == 200
    assert len(executions) == 2
    assert first.headers["cache-control"] == "no-store"


def test_session_scope_snapshot_and_failed_inputs_cannot_hit(client, formal_batch, executions):
    base, batch, _ = formal_batch(17)
    assert query(client, base, batch).status_code == 200
    assert query(client, base, batch, session=OTHER).status_code == 200
    assert len(executions) == 2
    assert (
        query(
            client,
            base,
            batch,
            scope_conditions=[
                {"dimension_code": "algorithm_data_structure", "operator": "lt", "value": 3}
            ],
        ).status_code
        == 200
    )
    assert len(executions) == 3
    assert query(client, base, batch, snapshot_id="not-the-snapshot").status_code == 409
    # A failed check invalidates that session's completed input, not authority.
    assert query(client, base, batch).status_code == 200
    assert len(executions) == 4


def test_changed_scores_with_same_marginal_statistics_are_not_reused(
    client, runtime, formal_batch, executions
):
    base, batch, _ = formal_batch(17)
    first = query(client, base, batch)
    assert first.status_code == 200
    with runtime.db.transaction(write=True) as c:
        # Fault injection only in pytest's disposable synthetic database.
        c.execute("DROP TRIGGER immutable_evaluations_update")
        rows = (
            c.execute(
                "SELECT rowid,score_units FROM evaluations WHERE batch_id=? "
                "AND dimension_code='algorithm_data_structure' ORDER BY score_units LIMIT 1",
                (batch["batch_id"],),
            ).fetchone(),
            c.execute(
                "SELECT rowid,score_units FROM evaluations WHERE batch_id=? "
                "AND dimension_code='algorithm_data_structure' ORDER BY score_units DESC LIMIT 1",
                (batch["batch_id"],),
            ).fetchone(),
        )
        for row, value in ((rows[0], rows[1]["score_units"]), (rows[1], rows[0]["score_units"])):
            c.execute(
                "UPDATE evaluations SET score_units=?,score_raw=? WHERE rowid=?",
                (value, str(value / 100), row["rowid"]),
            )
    second = query(client, base, batch)
    assert second.status_code == 200, second.text
    assert second.json()["scope"]["statistics"] == first.json()["scope"]["statistics"]
    assert len(executions) == 2


@pytest.mark.parametrize("revoke", ["token", "membership", "evidence"])
def test_warm_result_never_bypasses_current_revocation_or_integrity(
    client, runtime, formal_batch, executions, revoke
):
    base, batch, _ = formal_batch(17)
    assert query(client, base, batch).status_code == 200
    with runtime.db.transaction(write=True) as c:
        if revoke == "token":
            c.execute("UPDATE teacher_tokens SET revoked=1 WHERE teacher_id=?", (runtime.teacher,))
        elif revoke == "membership":
            c.execute("DELETE FROM scope_members WHERE teacher_id=?", (runtime.teacher,))
        else:
            c.execute("DROP TRIGGER immutable_evaluations_update")
            c.execute(
                "UPDATE evaluations SET score_cell='C9999' WHERE batch_id=?", (batch["batch_id"],)
            )
    response = query(client, base, batch)
    assert response.status_code == {"token": 401, "membership": 404, "evidence": 503}[revoke]
    assert "items" not in response.json()
    assert len(executions) == 1


def test_new_credential_and_expired_completed_entry_compute_again(
    client, runtime, formal_batch, executions
):
    base, batch, _ = formal_batch(17)
    assert query(client, base, batch).status_code == 200
    client.headers["Authorization"] = "Bearer " + runtime.admin.issue_token(runtime.teacher)
    assert query(client, base, batch).status_code == 200
    assert len(executions) == 2
    client.app.state.edu_diagnosis_reuse.clock = lambda: float("inf")
    assert query(client, base, batch).status_code == 200
    assert len(executions) == 3


def test_readonly_batch_grant_is_live_even_with_a_completed_result(
    client, runtime, formal_batch, executions
):
    base, batch, _ = formal_batch(17)
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    client.headers["Authorization"] = "Bearer " + runtime.other_token
    assert query(client, base, batch).status_code == 200
    runtime.admin.revoke_read(runtime.other, batch["batch_id"])
    assert query(client, base, batch).status_code == 404
    runtime.admin.grant_read(runtime.other, batch["batch_id"])
    assert query(client, base, batch).status_code == 200
    assert len(executions) == 2


def test_revocation_during_a_warm_projection_fails_and_discards(
    client, runtime, formal_batch, executions, monkeypatch
):
    base, batch, _ = formal_batch(17)
    assert query(client, base, batch).status_code == 200
    project = DiscoveryReader._response

    def revoke_after_projection(*args, **kwargs):
        result = project(*args, **kwargs)
        runtime.admin.remove_member(runtime.teacher, runtime.scope)
        return result

    monkeypatch.setattr(DiscoveryReader, "_response", revoke_after_projection)
    assert query(client, base, batch).status_code == 404
    assert len(executions) == 1
    monkeypatch.setattr(DiscoveryReader, "_response", project)
    runtime.admin.set_member(runtime.teacher, runtime.scope, can_import=True)
    assert query(client, base, batch).status_code == 200
    assert len(executions) == 2


def test_new_revision_has_an_independent_input_and_historical_access_is_preserved(
    client, formal_batch, executions
):
    base, batch, _ = formal_batch(17)
    original = query(client, base, batch)
    assert original.status_code == 200
    new_base, new_batch, _ = formal_batch(17, revision=batch["batch_id"], change=True)
    assert new_batch["snapshot_id"] != batch["snapshot_id"]
    revised = query(client, new_base, new_batch)
    assert revised.status_code == 200
    assert revised.json()["scope"]["input_key"] != original.json()["scope"]["input_key"]
    assert len(executions) == 2
    assert query(client, base, batch).status_code == 200
    assert len(executions) == 2
    assert query(client, base, batch, snapshot_id=new_batch["snapshot_id"]).status_code == 409
