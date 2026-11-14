"""Capture aggregate-only examples from real synthetic XLSX imports and the unchanged kernel."""

import argparse
import asyncio
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.exact_json import exact_wire
from app.edu.auth import authenticate
from app.edu.discoveries import DiscoveryReader, project
from app.edu.discovery_models import DiscoverySpec
from app.importing.edu import parse_excel
from fastapi.testclient import TestClient

from prototypes.adaptive_assessment.adapters import parse_contest_zip
from prototypes.adaptive_assessment.analysis import Kernel
from prototypes.adaptive_assessment.synthetic import contest_zip
from prototypes.capability_profiles.synthetic import workbook
from prototypes.teacher_agent_bridge.fixtures import build_host


async def capture(directory: Path, recipe: str) -> dict:
    host = build_host(directory, install_bridge=False)
    host.app.state.edu_service.parser = parse_excel
    raw = workbook(recipe)
    with TestClient(
        host.app, headers={"Authorization": "Bearer " + host.token}
    ) as client:
        response = client.post(
            "/api/v1/edu/imports/preview", files={"file": ("synthetic.xlsx", raw)}
        )
        assert response.status_code == 201, response.text
        preview = response.json()["import"]
        response = client.post(
            f"/api/v1/edu/imports/{preview['import_id']}/commit",
            json={
                "preview_version": preview["preview_version"],
                "acknowledge_warnings": True,
            },
        )
        assert response.status_code == 201, response.text
        ref = response.json()
        database = host.app.state.edu_service.database
        actor = authenticate(database, host.token)
        reader = DiscoveryReader(database)
        counts: Counter[str] = Counter()
        async with reader.calculation(
            actor, ref["batch_id"], DiscoverySpec(snapshot_id=ref["snapshot_id"])
        ) as (data, result):
            details = []
            for finding in result.findings:
                if counts[finding.type] >= (
                    2 if finding.type == "group_difference" else 1
                ):
                    continue
                details.append(project(finding, data).model_dump(mode="json"))
                counts[finding.type] += 1
            return {
                "recipe": recipe,
                "xlsx_sha256": sha256(raw).hexdigest(),
                "students": data.scope.scope_record_count,
                "retained_findings": len(result.findings),
                "details": details,
            }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    cases = []
    for recipe in ("profile-balanced-72", "profile-overlap-96"):
        with TemporaryDirectory(prefix="teacher-analysis-synthetic-") as directory:
            cases.append(asyncio.run(capture(Path(directory), recipe)))
    item_cases = []
    for participants, item_count in ((14, 2), (27, 7)):
        envelope = parse_contest_zip(
            contest_zip(participants=participants, items=item_count, missing=True)
        )
        kernel = Kernel(envelope)
        items, statistics = [], []
        for item in envelope.data.items:
            values, sources = kernel.statistics(item.item_id)
            average = values["mean"]
            items.append(
                {
                    "item_id": item.item_id,
                    "display_name": item.name,
                    "kind": item.kind,
                    "scale": {
                        "minimum": item.scale.minimum,
                        "maximum": item.scale.maximum,
                        "step": item.scale.step,
                        "unit": item.scale.unit,
                    }
                    if item.scale
                    else None,
                }
            )
            missing = values["states"].get("missing", 0)
            statistics.append(
                {
                    "item_id": item.item_id,
                    "valid_count": values["valid_count"],
                    "missing_count": missing,
                    "invalid_count": values["excluded_count"] - missing,
                    "mean": {
                        "value": float(average) if average is not None else None,
                        "exact": {
                            "numerator": average.numerator,
                            "denominator": average.denominator,
                        }
                        if average is not None
                        else None,
                        "display": str(average),
                    },
                    "distribution": [
                        {"score": float(score), "count": count}
                        for score, count in values["distribution"]
                    ],
                    "evidence_ids": list(sources.references),
                }
            )
        item_cases.append(
            {
                "origin": "existing_offline_canonical_kernel",
                "students": participants,
                "scope": {
                    "batchId": envelope.data.batch.batch_id,
                    "snapshotId": envelope.snapshot_id,
                },
                "items": items,
                "statistics": statistics,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            exact_wire(
                {"synthetic_only": True, "cases": cases, "item_cases": item_cases}
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            [
                {
                    "recipe": c["recipe"],
                    "students": c["students"],
                    "details": len(c["details"]),
                }
                for c in cases
            ]
        )
    )


if __name__ == "__main__":
    main()
