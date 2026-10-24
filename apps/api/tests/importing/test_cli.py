import json
import sys

from app.importing.edu.__main__ import main


def test_cli_summary_is_read_only(workbook, pack, tmp_path, monkeypatch, capsys):
    path = tmp_path / "synthetic.xlsx"
    data = pack(workbook)
    path.write_bytes(data)
    monkeypatch.setattr(sys, "argv", ["edu", str(path), "--summary-only"])
    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["detected_student_count"] == 2
    assert output["parsed_evaluation_count"] == 10
    assert path.read_bytes() == data
    assert list(tmp_path.iterdir()) == [path]


def test_cli_invalid_content_has_failure_exit_code(workbook, pack, tmp_path, monkeypatch, capsys):
    workbook["Sheet2"]["C3"] = "private-value"
    path = tmp_path / "synthetic.xlsx"
    path.write_bytes(pack(workbook))
    monkeypatch.setattr(sys, "argv", ["edu", str(path)])
    assert main() == 2
    output = capsys.readouterr().out
    assert "private-value" not in output
    assert json.loads(output)["status"] == "invalid"


def test_cli_read_failure_never_echoes_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["edu", str(tmp_path / "private-marker.xlsx")])
    assert main() == 2
    assert json.loads(capsys.readouterr().out) == {"error": {"code": "FILE_READ_FAILED"}}
