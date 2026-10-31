"""Real API integer diagnostics using an isolated database and generated workbook only.

Python JSON integers remain lossless. No credentials or student rows are written to
the diagnostic output; complete aggregate responses stay in ignored artifacts.
"""

import argparse
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.edu.auth import TeacherAdmin
from app.main import create_app
from app.storage.database import Database


SAFE_INTEGER = 2**53 - 1


def unsafe_integers(value: object, path: str = "$") -> list[dict[str, object]]:
    if type(value) is int and abs(value) > SAFE_INTEGER:
        return [
            {
                "path": path,
                "wire_type": "JSON integer",
                "integer": str(value),
                "expected": "JavaScript safe integer (current frontend decoder)",
                "digits": len(str(abs(value))),
            }
        ]
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            for item in unsafe_integers(child, f"{path}.{key}")
        ]
    if isinstance(value, list):
        return [
            item
            for index, child in enumerate(value)
            for item in unsafe_integers(child, f"{path}[{index}]")
        ]
    return []


def probe(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location(
        "synthetic_precision_fixture",
        Path(__file__).with_name("make-discovery-fixtures.py"),
    )
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    with TemporaryDirectory(prefix="weiran-integer-probe-") as temporary:
        root = Path(temporary)
        fixture.make(root, "precision105")
        database = Database(root / "synthetic.sqlite3")
        database.migrate()
        admin = TeacherAdmin(database)
        teacher = admin.create_teacher("synthetic-integer-probe")
        scope = admin.create_scope("合成精确整数验收")
        admin.set_member(teacher, scope, can_import=True)
        token = admin.issue_token(teacher, hours=1)
        application = create_app(
            Settings(environment="test", database_path=database.path)
        )
        diagnostics: list[dict[str, object]] = []
        with TestClient(application) as client:
            client.headers["Authorization"] = "Bearer " + token
            preview = client.post(
                "/api/v1/edu/imports/preview",
                data={
                    "data_scope_id": scope,
                    "title": "合成105人精度验收",
                },
                files={
                    "file": (
                        "precision105.xlsx",
                        (root / "precision105.xlsx").read_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
            assert preview.status_code == 201
            view = preview.json()
            assert view["summary"]["detected_student_count"] == 105
            assert view["summary"]["parsed_evaluation_count"] == 525
            assert view["summary"]["error_count"] == 0
            committed = client.post(
                f"/api/v1/edu/imports/{view['import']['import_id']}/commit",
                json={
                    "preview_version": view["import"]["preview_version"],
                    "acknowledge_warnings": True,
                },
            )
            assert committed.status_code == 201
            base = "/api/v1/edu/batches/" + committed.json()["batch_id"]
            batch = client.get(base).json()
            query = {
                "snapshot_id": batch["snapshot_id"],
                "profile": "edu.discovery.profile.v1",
                "scope_conditions": [],
            }
            response = client.post(
                base + "/discoveries/query", json={**query, "limit": 10}
            )
            assert response.status_code == 200
            listing = response.json()
            (output / "discovery-list.synthetic.json").write_text(
                response.text, encoding="utf-8"
            )
            diagnostics.append(
                {
                    "endpoint": "discoveries/query",
                    "status": 200,
                    "unsafe_integers": unsafe_integers(listing),
                }
            )
            assert listing["items"]
            for index, card in enumerate(listing["items"]):
                detail = client.post(
                    base + f"/discoveries/{card['discovery_id']}/query", json=query
                )
                assert detail.status_code == 200
                (output / f"discovery-detail-{index}.synthetic.json").write_text(
                    detail.text, encoding="utf-8"
                )
                diagnostics.append(
                    {
                        "endpoint": f"discoveries/{{id}}/query [index={index}]",
                        "status": 200,
                        "type": card["type"],
                        "unsafe_integers": unsafe_integers(detail.json()),
                    }
                )
            record = {
                "synthetic_only": True,
                "students": 105,
                "score_rows": 525,
                "preview_errors": 0,
                "responses": diagnostics,
            }
            (output / "integer-diagnostics.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    probe(parser.parse_args().output)
