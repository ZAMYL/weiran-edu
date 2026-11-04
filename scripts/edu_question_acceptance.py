"""Synthetic-only loopback HTTP receipt; never starts a teacher database or a model."""

import argparse
import http.client
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from fractions import Fraction
from io import BytesIO
from pathlib import Path

import uvicorn
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))


def workbook() -> bytes:
    book = Workbook()
    definitions = book.active
    definitions.title = "Sheet1"
    definitions.append(["edu-question-xlsx.v1", "题目名称", "最低分", "最高分", "步长", "单位"])
    keys = [f"Q{i:02d}" for i in range(1, 14)]
    for i, key in enumerate(keys, 1):
        definitions.append([key, f"合成题目{i}", 0, i * 10, 0.5 if i % 2 == 0 else 0.01, "分"])
    scores = book.create_sheet("Sheet2")
    scores.append(["参评记录", *keys])
    for i in range(20):
        scores.append([f"S{i:04d}", *([i / 2] * 13)])
    scores["B2"], scores["B3"], scores["C4"] = None, -1, 1.001
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def run() -> dict:
    from app.core.config import Settings
    from app.core.errors import APIError
    from app.edu.auth import TeacherAdmin, authenticate
    from app.edu.question_provider import QuestionAssessmentProvider
    from app.main import create_app
    from app.storage.database import Database

    records = []
    with tempfile.TemporaryDirectory(prefix="edu-v11-http-synthetic-") as folder:
        db = Database(Path(folder) / "synthetic.sqlite3")
        db.migrate()
        admin = TeacherAdmin(db)
        owner = admin.create_teacher("synthetic-local-owner")
        reader = admin.create_teacher("synthetic-local-reader")
        scope = admin.create_scope("合成授权测评范围；不代表同班或同校")
        admin.set_member(owner, scope, can_import=True)
        admin.set_member(reader, scope, can_import=False)
        token, reader_token = admin.issue_token(owner), admin.issue_token(reader)
        app = create_app(Settings(environment="test", database_path=db.path))
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(10)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        connection = None
        try:
            until = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() > until:
                    raise RuntimeError("Synthetic server did not start")
                time.sleep(0.01)
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=20)

            def request(
                method,
                path,
                expected=200,
                *,
                body=None,
                content_type="application/json",
                credential=None,
            ):
                response_body = json.dumps(body).encode() if isinstance(body, dict) else body
                connection.request(
                    method,
                    "/api/v1/edu" + path,
                    response_body,
                    headers={
                        "Authorization": "Bearer " + (credential or token),
                        "Content-Type": content_type,
                    },
                )
                response = connection.getresponse()
                value = json.loads(response.read())
                # Never put Authorization or credentials into the receipt.
                records.append(
                    {
                        "method": method,
                        "path": path,
                        "status": response.status,
                        "expected_status": expected,
                        "request_id": response.getheader("X-Request-ID"),
                        "response": value,
                    }
                )
                assert response.status == expected, (response.status, value)
                return value

            boundary = "edu-synthetic-items-boundary"
            upload = (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                    'filename="synthetic.xlsx"\r\nContent-Type: application/octet-stream\r\n\r\n'
                ).encode()
                + workbook()
                + f"\r\n--{boundary}--\r\n".encode()
            )
            content_type = f"multipart/form-data; boundary={boundary}"
            preview = request(
                "POST", "/imports/preview", 201, body=upload, content_type=content_type
            )
            assert preview["observation_count"] == 260 and len(preview["recognized_items"]) == 13
            assert preview["quality_counts"] == {
                "missing": 1,
                "out_of_range": 1,
                "off_step": 1,
                "valid": 257,
            }
            path = f"/imports/{preview['import_id']}"
            request("GET", path)
            request("GET", path + "/quality-report")
            ref = request(
                "POST",
                path + "/commit",
                201,
                body={"preview_version": preview["preview_version"], "acknowledge_warnings": True},
            )
            batch = f"/batches/{ref['batch_id']}"
            detail = request("GET", batch)
            assert detail["recognized_items"] == preview["recognized_items"]
            listed = request("GET", "/batches?include_dynamic=true")
            assert listed["items"][0]["batch_id"] == ref["batch_id"]
            stats = request("GET", batch + "/statistics?snapshot_id=" + ref["snapshot_id"])
            for item in stats["item_statistics"]:
                indexes = [
                    i
                    for i in range(20)
                    if not (item["item_id"] == "Q01" and i in (0, 1))
                    and not (item["item_id"] == "Q02" and i == 2)
                ]
                scores = [Fraction(i, 2) for i in indexes]
                assert Fraction(**item["mean"]["exact"]) == sum(scores, Fraction()) / len(scores)
                observed = {Fraction(**d["exact"]): d["count"] for d in item["distribution"]}
                assert observed == Counter(scores)
                assert item["denominator"] == 20 and item["valid_count"] == len(scores)
            single = request(
                "GET", batch + "/statistics?snapshot_id=" + ref["snapshot_id"] + "&item_id=Q01"
            )
            assert len(single["item_statistics"]) == 1
            evidence = request(
                "GET",
                batch
                + "/evidence/"
                + single["item_statistics"][0]["evidence_ids"][2]
                + "?snapshot_id="
                + ref["snapshot_id"],
            )
            assert evidence["source"]["cell"] == "B4" and evidence["exact"] == {
                "numerator": 1,
                "denominator": 1,
            }
            request("GET", batch + "/statistics?snapshot_id=wrong", 409)
            request("GET", batch + "/statistics", 404, credential=reader_token)
            admin.grant_read(reader, ref["batch_id"])
            request("GET", batch + "/statistics", credential=reader_token)
            admin.revoke_read(reader, ref["batch_id"])
            request("GET", batch + "/statistics", 404, credential=reader_token)
            duplicate = request(
                "POST", "/imports/preview", 201, body=upload, content_type=content_type
            )
            repeated = request(
                "POST",
                f"/imports/{duplicate['import_id']}/commit",
                body={
                    "preview_version": duplicate["preview_version"],
                    "acknowledge_warnings": True,
                },
            )
            assert repeated["result"] == "duplicate" and repeated["batch_id"] == ref["batch_id"]
            provider = QuestionAssessmentProvider(app.state.edu_service)
            principal = authenticate(db, token)
            lease = provider.acquire(principal, ref["batch_id"], ref["snapshot_id"])
            snapshot = provider.read_snapshot(lease)
            try:
                provider.acquire(
                    principal, ref["batch_id"], ref["snapshot_id"], purpose="external_model"
                )
            except APIError as error:
                assert error.code == "FORMAL_MODEL_CALL_DISABLED"
            else:
                raise AssertionError("Formal remote model purpose unexpectedly allowed")
            admin.revoke_tokens(owner)
            request("GET", batch + "/statistics", 401)
            with db.transaction() as c:
                assert c.execute("SELECT count(*) FROM question_items").fetchone()[0] == 13
                assert c.execute("SELECT count(*) FROM question_observations").fetchone()[0] == 260
            return {
                "synthetic_only": True,
                "transport": "literal-loopback TCP -> Uvicorn/FastAPI -> temporary SQLite",
                "request_count": len(records),
                "requests": records,
                "participant_count": 20,
                "observation_count": 260,
                "actual_items": 13,
                "independent_mean_and_distribution_checks": 13,
                "provider_observations_verified": len(snapshot.observations),
                "formal_model_call": "blocked",
                "real_qwen_calls": 0,
                "browser_executed": False,
            }
        finally:
            if connection:
                connection.close()
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            if thread.is_alive():
                raise RuntimeError("Synthetic server did not stop")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("New output path required")
    result = run()
    result["source_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({k: v for k, v in result.items() if k != "requests"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
