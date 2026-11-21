"""Temporary Linux diagnostic for the offline synthetic workbook regression."""
import json
import os
from collections import Counter
from random import Random
from uuid import UUID

TARGET = "test_varied_formal_workbooks_are_isolated_and_match_database_scores"
AUDIT_KEYS = (
    "raw_qualified_findings", "returned_findings", "group_discoveries",
    "distinct_partitions", "profile_candidates", "filters",
)


def pytest_collection_modifyitems(items):
    target = next((item for item in items if item.name == TARGET), None)
    if target is None:
        return
    module = target.module
    host, start = module.variable_host, module.start
    measurements = {}
    seed = os.environ.get("I05_UUID_SEED")

    def observed_host(directory, cases):
        if seed:
            import app.edu.service as edu_service
            rng = Random(int(seed))
            edu_service.uuid4 = lambda: UUID(int=rng.getrandbits(128), version=4)
        measurements.update(cases)
        return host(directory, cases)

    def observed_start(client, fixture, service, case, *args, **kwargs):
        output = start(client, fixture, service, case, *args, **kwargs)
        batch, _, _, _, view = output
        with service.store.database.transaction() as connection:
            stored = connection.execute(
                "SELECT bs.display_order,s.public_id,e.dimension_code,e.score_units "
                "FROM evaluations e JOIN students s USING(student_pk) "
                "JOIN batch_students bs ON bs.student_pk=e.student_pk AND bs.batch_id=e.batch_id "
                "WHERE e.batch_id=?", (batch["batch_id"],),
            ).fetchall()
        rows = measurements[case]
        mismatches = sum(
            r["score_units"] != int(rows[r["display_order"] - 1][codes.index(r["dimension_code"])] * 100)
            for r in stored
        ) if (codes := [dimension.code for dimension in module.DIMENSIONS]) else None
        assert len(stored) == 5 * len(rows) and mismatches == 0
        ids = sorted({(r["public_id"], r["display_order"]) for r in stored})
        smallest = min(row[0] for row in rows)
        fold_sizes = Counter(offset % 3 for offset, (_, position) in enumerate(ids)
                             if rows[position - 1][0] == smallest)
        support = sum(fold_sizes.values())
        projection = (view["result"] or {}).get("profile_projection") or {}
        audit = projection.get("search_audit") or {}
        print("I05_PROFILE_PROBE=" + json.dumps({
            "seed": seed, "case": case, "sql_cells": len(stored), "mismatches": mismatches,
            "minority_retained_by_fold": [support - fold_sizes[fold] for fold in range(3)],
            "profiles": len(projection.get("profiles", [])), "stop": projection.get("stop_reason"),
            "audit": {key: audit.get(key) for key in AUDIT_KEYS},
            "model_requests": view["model_requests"], "tool_attempts": view["tool_attempts"],
        }, sort_keys=True))
        return output

    module.variable_host, module.start = observed_host, observed_start
