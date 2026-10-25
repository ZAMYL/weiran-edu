"""Developer tools: independent oracles, synthetic files and fail-closed acceptance."""

import json
import sys
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
from edu_demo_fixtures import (  # noqa: E402
    CODES,
    main,
    make_fixture,
    safe_directory,
    write_new,
)
from edu_demo_smoke import (  # noqa: E402
    CheckFailure,
    Smoke,
    conditions,
    dimension_oracle,
    exact_value,
    local_url,
    selected_indexes,
)
from edu_demo_smoke import main as smoke_main  # noqa: E402

from app.importing.edu import build_quality_report, parse_excel


@pytest.mark.parametrize("size", [17, 53, 105])
@pytest.mark.parametrize("seed", [0, 42, 713])
def test_synthetic_workbook_deterministic_and_exact(size, seed):
    first, second = make_fixture(size, seed), make_fixture(size, seed)
    assert first.scores == second.scores
    parsed = parse_excel(first.data)
    repeat = parse_excel(second.data)
    assert parsed.content_sha256 == repeat.content_sha256
    assert parsed.can_commit and not parsed.issues
    assert len(parsed.students) == size
    for index, student in enumerate(parsed.students):
        assert student.name == f"合成占位{index + 1}"
        assert student.student_number == f"{index + 1:012d}"
        assert len(student.evaluations) == 5
        assert tuple(Decimal(str(e.score_raw)) for e in student.evaluations) == first.scores[index]
    assert first.scores[1][0] == Decimal("2.75")
    assert first.scores != make_fixture(size, seed + 1).scores
    workbook = load_workbook(BytesIO(first.data))
    assert workbook.sheetnames == ["Sheet1", "Sheet2"]
    assert workbook["Sheet2"]["C9"].number_format == "0.00"
    assert not any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row)


@pytest.mark.parametrize(
    "case,status,codes",
    [
        ("valid", "passed", set()),
        ("warning", "warning", {"COMMENT_EMPTY", "COMMENT_REPEATED"}),
        ("blocked", "blocked", {"SCORE_OUT_OF_RANGE", "SCORE_PRECISION"}),
    ],
)
def test_real_parser_quality_cases(case, status, codes):
    fixture = make_fixture(17, 42, case)
    result = parse_excel(fixture.data)
    report = build_quality_report(result)
    assert report.status == status
    assert {issue.code for issue in result.issues} == codes
    if case == "blocked":
        assert not result.can_commit
        assert result.students[0].evaluations[0].score_units is None
        assert result.students[0].evaluations[1].score_units is None
        with ZipFile(BytesIO(fixture.data)) as archive:
            xml = archive.read("xl/worksheets/sheet2.xml")
            assert b"<v>2.751</v>" in xml and b"<v>5.01</v>" in xml
    else:
        assert result.can_commit


@pytest.mark.parametrize("size", [0, -1, 1667, True])
def test_student_budget(size):
    with pytest.raises(ValueError):
        make_fixture(size, 42)


def test_safe_output_exclusive_and_no_teacher_file_read(tmp_path):
    fixture = make_fixture(17, 42)
    output = write_new(tmp_path, fixture.filename, fixture.data)
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        write_new(tmp_path, fixture.filename, b"replacement")
    assert output.read_bytes() == before
    for filename in ("../escape.xlsx", "nested/file.xlsx", "C:stream", "nested\\file.xlsx"):
        with pytest.raises(ValueError):
            write_new(tmp_path, filename, b"bad")
    assert main(["--students", "17", "--seed", "42", "--output-dir", str(tmp_path)]) == 2
    assert output.read_bytes() == before


def test_repository_relative_and_missing_output_rejected(tmp_path):
    for path in (
        Path("relative"),
        Path(__file__).resolve().parents[4],
        tmp_path / "absent",
        tmp_path / "..",
    ):
        with pytest.raises((ValueError, OSError)):
            safe_directory(path)
    (tmp_path / ".git").write_text("gitdir: fictional", encoding="utf-8")
    with pytest.raises(ValueError):
        safe_directory(tmp_path)


def test_symlink_output_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not grant symlink creation")
    with pytest.raises(ValueError):
        safe_directory(link)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost:8000",
        "http://127.0.0.1@evil.test",
        "http://127.0.0.1/?token=secret",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1/path",
        "http://127.0.0.1/#fragment",
        "http://127.0.0.1:invalid",
    ],
)
def test_no_remote_or_credential_urls(url):
    with pytest.raises(ValueError):
        local_url(url)


def test_independent_oracle_has_known_boundary_values():
    fixture = make_fixture(3, 42)
    oracle = dimension_oracle(fixture, CODES[0])
    assert oracle["mean"] == Decimal("2.75")
    assert oracle["median"] == Decimal("2.75")
    assert selected_indexes(fixture, conditions("lt")) == [0]
    assert selected_indexes(fixture, conditions("lte")) == [0, 1]
    assert selected_indexes(fixture, conditions("eq")) == [1]
    with pytest.raises(CheckFailure):
        exact_value(
            {"exact": {"numerator": 3, "denominator": 1}, "value": 3, "display": "3.00"},
            Decimal("2.75"),
        )


def bridge(client, mutate=None):
    def handle(request):
        response = client.request(
            request.method,
            str(request.url),
            content=request.content,
            headers={
                **dict(request.headers),
                "Authorization": request.headers.get("Authorization", ""),
            },
        )
        body = response.json()
        if mutate:
            mutate(request, response, body)
        return httpx.Response(response.status_code, headers=dict(response.headers), json=body)

    return httpx.Client(base_url="http://testserver", transport=httpx.MockTransport(handle))


@pytest.mark.parametrize(
    "size,seed,case", [(17, 42, "valid"), (53, 713, "warning"), (17, 0, "blocked")]
)
def test_offline_full_acceptance_adapter(client, runtime, size, seed, case):
    # Only the test adapter uses TestClient. The CLI constructs an ordinary network client.
    with bridge(client) as connection:
        smoke = Smoke(connection, runtime.token, make_fixture(size, seed, case), runtime.scope)
        smoke.run()
    assert all(s["passed"] for s in smoke.steps)
    assert smoke.counts["students"] == size
    safe = json.dumps(smoke.steps)
    for secret in (
        runtime.token,
        runtime.teacher,
        runtime.scope,
        "合成占位",
        "纯合成评语",
        "000000000001",
    ):
        assert secret not in safe


def test_duplicate_run_reuses_batch(client, runtime):
    with bridge(client) as connection:
        for expected in (0, 1):
            smoke = Smoke(connection, runtime.token, make_fixture(3, 42), runtime.scope)
            smoke.run()
            assert smoke.counts["duplicate_batch_reused"] == expected


@pytest.mark.parametrize(
    "fault",
    ["invalid_token", "no_import_permission", "snapshot_conflict", "wrong_mean", "wrong_source"],
)
def test_acceptance_stops_on_auth_snapshot_or_false_results(client, runtime, fault):
    def mutate(request, response, body):
        if fault == "snapshot_conflict" and request.url.path.endswith("/statistics"):
            response.status_code = 409
            body.clear()
            body.update(
                error={"code": "SNAPSHOT_MISMATCH", "request_id": response.headers["X-Request-ID"]}
            )
        elif fault == "wrong_mean" and request.url.path.endswith("/statistics"):
            body["dimensions"][0]["mean"]["exact"]["numerator"] += 1
        elif fault == "wrong_source" and request.url.path.endswith("/evidence"):
            body["items"][0]["source"]["file_sha256"] = "0" * 64

    if fault == "no_import_permission":
        runtime.admin.set_member(runtime.teacher, runtime.scope, can_import=False)
    with bridge(client, mutate) as connection:
        smoke = Smoke(
            connection,
            "invalid" if fault == "invalid_token" else runtime.token,
            make_fixture(3, 42),
            runtime.scope,
        )
        with pytest.raises(CheckFailure):
            smoke.run()
    assert smoke.steps[-1]["passed"] is False
    assert smoke.steps[-1]["failure_code"]
    assert not any(s["step"] == "formal_insights_and_metric_sources" for s in smoke.steps)


def test_cli_failure_summary_is_sanitized_and_nonzero(tmp_path, monkeypatch, capsys):
    import edu_demo_smoke

    secret = "never-print-token-or-private-response"
    original_client = httpx.Client

    def handle(request):
        if request.url.path == "/openapi.json":
            return httpx.Response(
                200,
                json={
                    "paths": {
                        "/api/v1/edu" + suffix: {method: {}}
                        for suffix, method in (
                            ("/me", "get"),
                            ("/scopes", "get"),
                            ("/imports/preview", "post"),
                            ("/batches/{batch_id}/insights/query", "post"),
                            ("/batches/{batch_id}/insights/{candidate_id}/records/query", "post"),
                        )
                    }
                },
            )
        return httpx.Response(
            401,
            headers={"X-Request-ID": "req_synthetic", "Cache-Control": "no-store"},
            json={
                "error": {"code": "UNAUTHORIZED", "request_id": "req_synthetic", "message": secret}
            },
        )

    monkeypatch.setenv("WEIRAN_EDU_DEMO_TOKEN", secret)
    monkeypatch.setattr(
        edu_demo_smoke.httpx,
        "Client",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    result = smoke_main(["--base-url", "http://127.0.0.1:8000", "--output-dir", str(tmp_path)])
    assert result == 2
    output = capsys.readouterr().out
    assert secret not in output
    summary = json.loads(output)
    assert not summary["passed"] and summary["flow"] == "incomplete"
    assert summary["steps"][-1]["http_checks"][-1]["status"] == 401
    saved = list(tmp_path.glob("v1-smoke-summary-*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text()) == summary
