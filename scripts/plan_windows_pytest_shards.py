"""Plan Windows pytest shards from a trusted collect-only manifest; never run tests.

Group by whole file to preserve fixtures. Fail on unknown directories, duplicate IDs,
missing assignments or overlaps. No test skipping, threshold changes or dependencies.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath

GROUPS = {
    "core": {"analysis", "code", "comments", "discovery", "insights"},
    "ai_research": {"adaptive_assessment", "ai_edu", "research_v2", "research_v22"},
    "edu_import": {"edu", "importing"},
    "reporting": {"reporting"},
    "teacher_bridge": {"durable_teacher_agent", "teacher_agent_bridge"},
}


def plan(record: dict) -> dict:
    revision, nodes = record["source_sha"], record["nodes"]
    if not re.fullmatch(r"[a-f0-9]{40}", revision) or not nodes or len(nodes) != len(set(nodes)):
        raise ValueError("invalid_revision_or_nonunique_collection")
    groups = {name: {"nodes": [], "files": set()} for name in GROUPS}
    for node in nodes:
        file = node.split("::", 1)[0].replace("\\", "/")
        path = PurePosixPath(file)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.parts[0] != "tests"
            or path.suffix != ".py"
        ):
            raise ValueError("untrusted_collection_path")
        if len(path.parts) == 2:
            owners = ["core"]
        else:
            owners = [name for name, dirs in GROUPS.items() if path.parts[1] in dirs]
        if len(owners) != 1:
            raise ValueError("unknown_or_overlapping_test_directory")
        group = groups[owners[0]]
        group["nodes"].append(node)
        group["files"].add(file)
    assigned = [n for g in groups.values() for n in g["nodes"]]
    if Counter(assigned) != Counter(nodes):
        raise ValueError("incomplete_shard_plan")
    output = {}
    for name, group in groups.items():
        output[name] = {
            "test_count": len(group["nodes"]),
            "files": sorted(group["files"]),
            "node_ids": group["nodes"],
            "argv": [
                "uv",
                "run",
                "--locked",
                "--no-sync",
                "pytest",
                "-q",
                "-ra",
                "--durations=30",
                "--junitxml=artifacts/" + name + ".xml",
                *sorted(group["files"]),
            ],
        }
    return {
        "source_sha": revision,
        "total_tests": len(nodes),
        "total_files": len({n.split("::", 1)[0] for n in nodes}),
        "collection_sha256": hashlib.sha256(
            json.dumps(sorted(nodes), ensure_ascii=False).encode()
        ).hexdigest(),
        "missing": [],
        "overlaps": [],
        "executed": False,
        "job_timeout_minutes": 15,
        "pytest_working_directory": "apps/api",
        "groups": output,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = plan(json.loads(args.manifest.read_text(encoding="utf-8")))
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "total_tests": result["total_tests"],
                "groups": {n: g["test_count"] for n, g in result["groups"].items()},
                "executed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
