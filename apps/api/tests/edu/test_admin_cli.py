import json
import sys

import pytest

from app.core.errors import APIError
from app.edu.__main__ import main
from app.edu.auth import authenticate
from app.storage.database import Database


def test_operator_cli_initializes_and_issues_then_revokes_teacher_credential(
    tmp_path, monkeypatch, capsys
):
    database = tmp_path / "synthetic.sqlite3"

    def invoke(*arguments):
        monkeypatch.setattr(sys, "argv", ["edu-admin", "--database", str(database), *arguments])
        result = main()
        return result, json.loads(capsys.readouterr().out)

    assert invoke("init")[0] == 0
    status, teacher = invoke("teacher-create", "--subject", "synthetic-operator-fixture")
    assert status == 0
    status, credential = invoke("token-issue", "--teacher-id", teacher["teacher_id"])
    assert status == 0 and credential["expires_in_seconds"] == 86400
    db = Database(database)
    assert authenticate(db, credential["access_token"]).teacher_id == teacher["teacher_id"]
    assert invoke("token-revoke", "--teacher-id", teacher["teacher_id"])[0] == 0
    with pytest.raises(APIError):
        authenticate(db, credential["access_token"])


def test_admin_errors_do_not_echo_path_or_private_subject(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "edu-admin",
            "--database",
            str(tmp_path / "private-marker.sqlite3"),
            "teacher-create",
            "--subject",
            "private-marker",
        ],
    )
    assert main() == 2
    assert json.loads(capsys.readouterr().out) == {"error": {"code": "ADMIN_OPERATION_FAILED"}}
