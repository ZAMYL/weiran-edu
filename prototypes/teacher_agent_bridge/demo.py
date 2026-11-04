"""Real loopback TCP -> FastAPI -> temporary SQLite -> Research/HTTP Mock -> evidence."""

import argparse
import json
import socket
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import uvicorn

from .fixtures import build_host


def exercise(base: str, fixture) -> dict:
    checks = []
    runs = {}
    with httpx.Client(
        base_url=base, headers={"Authorization": "Bearer " + fixture.token}, timeout=120
    ) as client:

        def request(method, path, expected=200, **kwargs):
            response = client.request(method, path, **kwargs)
            checks.append(
                {
                    "method": method,
                    "route": path.split("?")[0],
                    "status": response.status_code,
                    "expected": expected,
                }
            )
            if response.status_code != expected:
                raise AssertionError(
                    f"HTTP_EXPECTATION:{len(checks)}:{response.status_code}:{response.text[:180]}"
                )
            return response

        def commit(case):
            p = request(
                "POST",
                "/api/v1/edu/imports/preview",
                201,
                files={
                    "file": (
                        "synthetic.xlsx",
                        fixture.books[case],
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            ).json()
            return request(
                "POST",
                f"/api/v1/edu/imports/{p['import']['import_id']}/commit",
                201,
                json={
                    "preview_version": p["import"]["preview_version"],
                    "acknowledge_warnings": True,
                },
            ).json()

        for case in ("normal", "evidence", "empty", "fixed"):
            batch = commit(case)
            batchid = batch["batch_id"]
            snapshot = batch["snapshot_id"]
            question = (
                "为什么 算法与数据结构 与 代码实现能力 相关"
                if case == "evidence"
                else "识别值得优先核查的评分异常"
                if case == "empty"
                else "比较 算法与数据结构、代码实现能力 的组合与 问题建模能力 的群体差异"
            )
            strategy = "fixed" if case == "fixed" else "adaptive"
            run = request(
                "POST",
                f"/api/v1/edu/batches/{batchid}/ai-investigations",
                json={"snapshot_id": snapshot, "question": question, "strategy": strategy},
            ).json()
            runs[case] = run
            request(
                "POST",
                f"/api/v1/edu/batches/{batchid}/ai-investigations",
                409,
                json={"snapshot_id": snapshot, "question": question},
            )
            if case == "normal":
                fact = next(f for f in run["facts"] if f["method"] == "compare_student_groups")
                source = next(s for s in fact["source_refs"] if s["metric_key"] == "target.valid")
                query = {
                    "snapshot_id": snapshot,
                    "fact_id": fact["fact_id"],
                    "metric_key": source["metric_key"],
                    "limit": 3,
                }
                records = request(
                    "POST",
                    f"/api/v1/edu/batches/{batchid}/ai-investigations/{run['run_id']}/records/query",
                    json=query,
                ).json()
                assert records["total_count"] == 26 and len(records["items"]) == 3
                for link in records["items"]:
                    proof = request(
                        "GET", link["evidence_path"], params={"snapshot_id": snapshot}
                    ).json()
                    assert (
                        proof["evidence_id"] == link["evidence_id"]
                        and proof["student_id"] == link["student_id"]
                    )
                    assert (
                        proof["score"] == 1
                        and proof["comment_status"] == "withheld"
                        and proof["comment"] is None
                    )
                # Existing exploration and report flow must recompute via their own contracts.
                group = request(
                    "POST",
                    f"/api/v1/edu/batches/{batchid}/groups/query",
                    json={"snapshot_id": snapshot, "conditions": fact["conditions"]},
                ).json()
                assert group["matched_count"] == 26
                saved = request(
                    "POST",
                    f"/api/v1/edu/batches/{batchid}/analysis-results",
                    201,
                    json={
                        "snapshot_id": snapshot,
                        "conditions": fact["conditions"],
                        "title": "合成AI调查后的群体核查",
                        "candidate_ids": [],
                    },
                ).json()
                for fmt in ("md", "xlsx"):
                    file = request(
                        "GET",
                        f"/api/v1/edu/batches/{batchid}/analysis-results/{saved['result_id']}/export",
                        params={
                            "snapshot_id": snapshot,
                            "expected_version": saved["version"],
                            "format": fmt,
                        },
                    )
                    assert file.content and file.headers["cache-control"] == "no-store"
                runs["existing_flow"] = {
                    "group_count": 26,
                    "report_formats": ["md", "xlsx"],
                    "agent_fact_imported_into_report": False,
                }
        revoked = commit("revoked")
        fixture.admin.revoke_tokens(fixture.teacher_id)
        request(
            "POST",
            f"/api/v1/edu/batches/{revoked['batch_id']}/ai-investigations",
            401,
            json={"snapshot_id": revoked["snapshot_id"], "question": "识别值得优先核查的评分异常"},
        )
    assert runs["evidence"]["stop_reason"] == "needs_external_evidence"
    assert not runs["empty"]["selected_fact_ids"]
    return {
        "origin": "real_loopback_http_synthetic_only",
        "real_qwen": "not_run",
        "checks": checks,
        "runs": runs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("new_output_path_required")
    with TemporaryDirectory(prefix="weiran-authorized-ai-") as temporary:
        fixture = build_host(Path(temporary))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                fixture.app, host="127.0.0.1", port=port, log_level="critical", access_log=False
            )
        )
        worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("local_http_server_unavailable")
            result = exercise(f"http://127.0.0.1:{port}", fixture)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(
                json.dumps(
                    {
                        "checks": len(result["checks"]),
                        "passed": all(c["status"] == c["expected"] for c in result["checks"]),
                        "real_qwen": "not_run",
                    }
                )
            )
        finally:
            fixture.admin.revoke_tokens(fixture.teacher_id)
            server.should_exit = True
            worker.join(10)
            sock.close()
            if worker.is_alive():
                raise RuntimeError("temporary_server_cleanup_failed")


if __name__ == "__main__":
    main()
