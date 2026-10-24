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


def test_cli_exports_every_issue_and_keeps_source_unchanged(
    workbook, pack, tmp_path, monkeypatch, capsys
):
    for row in range(3, 8):
        workbook["Sheet2"].cell(row, 4).value = "隐藏的重复合成评语"
    source = tmp_path / "source.xlsx"
    data = pack(workbook)
    source.write_bytes(data)
    output = tmp_path / "quality.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["edu", str(source), "--quality-report", "--issue-limit", "1", "--output", str(output)],
    )
    assert main() == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert len(report["issues"]) == report["summary"]["warning_count"] == 5
    assert report["status"] == "warning" and report["validation_passed"]
    assert "隐藏的重复合成评语" not in output.read_text(encoding="utf-8")
    assert source.read_bytes() == data
    assert json.loads(capsys.readouterr().out)["written"]


def test_cli_refuses_source_overwrite_and_existing_output(
    workbook, pack, tmp_path, monkeypatch, capsys
):
    source = tmp_path / "source.xlsx"
    data = pack(workbook)
    source.write_bytes(data)
    monkeypatch.setattr(sys, "argv", ["edu", str(source), "--output", str(source)])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "OUTPUT_IS_SOURCE"
    assert source.read_bytes() == data
    output = tmp_path / "existing.json"
    output.write_text("original", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["edu", str(source), "--output", str(output)])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "OUTPUT_EXISTS"
    assert output.read_text(encoding="utf-8") == "original"


def test_cli_pagination_and_blocked_quality_report(workbook, pack, tmp_path, monkeypatch, capsys):
    workbook["Sheet2"]["C3"] = None
    source = tmp_path / "source.xlsx"
    source.write_bytes(pack(workbook))
    monkeypatch.setattr(
        sys,
        "argv",
        ["edu", str(source), "--row-offset", "5", "--row-limit", "1", "--student-limit", "1"],
    )
    assert main() == 2
    preview = json.loads(capsys.readouterr().out)
    assert len(preview["rows"]) == 1 and preview["row_page"]["offset"] == 5
    assert preview["summary"]["parsed_evaluation_count"] == 10
    assert len(preview["student_samples"]) == 1
    monkeypatch.setattr(sys, "argv", ["edu", str(source), "--quality-report"])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"


def test_cli_exports_readable_quality_report(workbook, pack, tmp_path, monkeypatch, capsys):
    workbook["Sheet2"]["C3"] = None
    source, output = tmp_path / "source.xlsx", tmp_path / "quality.md"
    source.write_bytes(pack(workbook))
    monkeypatch.setattr(
        sys,
        "argv",
        ["edu", str(source), "--quality-report", "--format", "markdown", "--output", str(output)],
    )
    assert main() == 2
    assert "存在阻断错误" in output.read_text(encoding="utf-8")
    assert "C3" in output.read_text(encoding="utf-8")
    assert json.loads(capsys.readouterr().out)["written"]
