"""Read-only adapter to an explicitly identified checkout. No oracle arithmetic here."""

import cProfile
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from .contracts import (
    DIMS,
    Binding,
    CandidateInput,
    CandidateRun,
    Claim,
    Predicate,
    Query,
    evidence,
    identity,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    ).stdout.strip()


class Baseline:
    def __init__(self, root: Path, expected_sha: str | None = None, *, external: bool = False):
        self.available = False
        self.metadata = {"revision": _git(root, "rev-parse", "HEAD"), "available": False}
        root = root.resolve()
        if external and expected_sha is None:
            raise ValueError("explicit_sut_requires_expected_sha")
        if expected_sha is not None and self.metadata["revision"] != expected_sha:
            raise ValueError("sut_revision_mismatch")
        module = root / "apps/api/app/analysis/edu/discovery/__init__.py"
        if not module.is_file():
            self.metadata["reason"] = "discovery_module_not_merged"
            return
        dirty = bool(_git(root, "status", "--porcelain"))
        if external and dirty:
            raise ValueError("sut_checkout_must_be_clean")
        self.metadata["working_tree_dirty"] = dirty
        if external and any(name == "app" or name.startswith("app.") for name in sys.modules):
            raise ValueError("external_sut_requires_fresh_interpreter")
        self.api = root / "apps/api"
        sys.path.insert(0, str(self.api))
        from app.analysis.edu import (
            AssessmentRecord,
            Condition,
            Dimension,
            EvidenceKind,
            EvidenceRef,
            Operator,
            ScorePolicy,
            prepare_dataset,
        )
        from app.analysis.edu.discovery import BoundaryRequest, Policy, discover, sources
        from app.analysis.edu.discovery.models import VERSION
        from app.analysis.edu.models import PairCorrelation

        self.Record = AssessmentRecord
        self.Condition = Condition
        self.Dimension = Dimension
        self.Evidence = EvidenceRef
        self.Kind = EvidenceKind
        self.Operator = Operator
        self.ScorePolicy = ScorePolicy
        self.prepare = prepare_dataset
        self.Boundary = BoundaryRequest
        self.Policy = Policy
        self.discover = discover
        self.sources = sources
        self.Pair = PairCorrelation
        self.version = VERSION
        self.available = True
        self.metadata.update(
            {"available": True, "rule_version": VERSION, "pinned_external_checkout": external}
        )

    def _conditions(self, conditions):
        return tuple(
            self.Condition(
                self.Dimension[p.dimension], self.Operator(p.operator), Fraction(p.threshold)
            )
            for p in conditions
        )

    def _rows(self, case):
        result = []
        for foreign, rows in ((False, case.rows), (True, case.distractors)):
            for i, row in enumerate(rows):
                batch, student, record, source = identity(case, i)
                if foreign:
                    batch += ":foreign"
                refs = tuple(
                    self.Evidence(
                        source, evidence(case, i, d)[1], self.Kind.SCORE, self.Dimension[d]
                    )
                    for d in DIMS
                )
                result.append(
                    self.Record(
                        batch,
                        student,
                        record,
                        source,
                        {self.Dimension[d]: raw for d, raw in zip(DIMS, row, strict=True)},
                        refs,
                    )
                )
        return self.prepare(tuple(result), policy=self.ScorePolicy(step=Fraction(1, 100)))

    def _binding(self, ref, refs):
        return Binding(
            (ref.batch_id, ref.student_id, ref.record_id, ref.source_id),
            tuple(
                (e.source_id, e.location, e.kind.value, e.dimension.name if e.dimension else "")
                for e in refs
            ),
        )

    @staticmethod
    def _stats(stats):
        return {
            "valid": stats.valid_count,
            "missing": stats.missing_count,
            "invalid": stats.invalid_count,
            "mean": stats.mean,
            "median": stats.median,
            "minimum": stats.minimum,
            "maximum": stats.maximum,
            "distribution": tuple(
                (f.score, f.share.count, f.share.denominator) for f in stats.distribution
            ),
        }

    def _relation(self, pair):
        return {
            "paired_count": pair.paired_count,
            "excluded_count": len(pair.excluded_records),
            "state": pair.state.value,
            "rho": pair.coefficient,
        }

    def _claim(self, case, run_id, item):
        p = item.payload
        if item.type == "dimension_relation":
            query = Query("relation", p.left.name, p.right.name)
            values = self._relation(p)
        elif item.type == "group_difference":
            if p.outcome_dimension is None:
                raise ValueError("sut_group_outcome_missing")
            query = Query(
                "group",
                conditions=tuple(
                    Predicate(c.dimension.name, c.operator.value, str(c.threshold))
                    for c in p.selection.conditions
                ),
                outcome=p.outcome_dimension.name,
            )
            sel = p.selection
            values = {
                "target_count": len(sel.matches),
                "remainder_count": len(p.remainder),
                "unevaluable_count": len(sel.unevaluable_records),
                "population_count": sel.scope.student_count,
                "eligible_count": sel.eligible_count,
                "share_total": sel.matched_share.proportion,
                "share_eligible": sel.matched_among_eligible.proportion,
            }
            for d in p.dimensions:
                values[d.dimension.name] = {
                    "target": self._stats(d.target),
                    "remainder": self._stats(d.remainder),
                    "difference": d.difference,
                    "condition_defined": d.condition_defined,
                }
        elif item.type == "rare_combination":
            query = Query("rare", p.left.name, p.right.name, side=p.side)
            values = {
                "pair_count": p.pair_count,
                "excluded_count": p.excluded_count,
                "q1": p.q1,
                "q3": p.q3,
                "median": p.median,
                "lower_fence": p.lower_fence,
                "upper_fence": p.upper_fence,
                "match_count": len(p.matches),
                "distribution": p.distribution,
                "pattern": p.pattern,
            }
        elif item.type == "boundary_sensitivity":
            conditions = tuple(
                Predicate(c.dimension.name, c.operator.value, str(c.threshold))
                for c in p.inclusive.conditions
            )
            varying = next(
                a.dimension.name
                for a, b in zip(p.strict.conditions, p.inclusive.conditions, strict=True)
                if a.operator != b.operator
            )
            query = Query("boundary", left=varying, conditions=conditions)
            values = {
                "strict_count": len(p.strict.matches),
                "inclusive_count": len(p.inclusive.matches),
                "added_count": len(p.added),
                "removed_count": len(set(p.strict.matches) - set(p.inclusive.matches)),
                "population_count": p.strict.scope.student_count,
                "eligible_count": p.strict.eligible_count,
                "unevaluable_count": len(p.strict.unevaluable_records),
                "observed_boundary": p.observed_boundary,
            }
        else:
            raise ValueError("unsupported_sut_finding")
        bindings = {
            s.metric_key: tuple(
                self._binding(ref, refs) for ref, refs in self.sources(item, s.metric_key)
            )
            for s in item.sources
        }
        return Claim(
            f"{run_id}:{item.discovery_id}",
            run_id,
            case.batch_id,
            case.snapshot_id,
            item.rule_version,
            query,
            values,
            bindings,
        )

    def run(self, case: CandidateInput, profile: str) -> CandidateRun:
        run_id = f"offline-{uuid4().hex}"
        if not self.available:
            return CandidateRun(run_id, "not_run", str(self.metadata["reason"]))
        if profile not in ("A_default", "B_full"):
            raise ValueError("unknown_baseline_profile")
        boundaries = tuple(
            self.Boundary(self._conditions(q.conditions), self.Dimension[q.left])
            for q in case.boundary_requests
            if q.kind == "boundary"
        )
        dataset = self._rows(case)
        profiler = cProfile.Profile()
        started = perf_counter()
        with profiler:
            result = self.discover(
                dataset,
                batch_id=case.batch_id,
                snapshot_id=case.snapshot_id,
                policy=self.Policy(search_budget=20 if profile == "A_default" else 50),
                boundaries=boundaries,
            )
        elapsed = perf_counter() - started
        names = {
            "discover",
            "compare_groups",
            "compare_boundary",
            "summarize",
            "dimension_stats",
            "select_students",
            "spearman_correlations",
            "_pair",
            "average_ranks",
        }
        operations = {}
        for entry in profiler.getstats():
            code = entry.code
            if hasattr(code, "co_filename") and code.co_name in names:
                path = Path(code.co_filename).resolve()
                if path.is_relative_to(self.api.resolve()):
                    key = f"{path.relative_to(self.api).as_posix()}:{code.co_name}"
                    operations[key] = entry.callcount
        claims = [self._claim(case, run_id, item) for item in result.findings]
        selected = tuple(c.fact_id for c in claims[:3])
        existing = {(c.query.left, c.query.right) for c in claims if c.query.kind == "relation"}
        for i, pair in enumerate(result.correlations.pairs):
            if (pair.left.name, pair.right.name) in existing:
                continue
            query = Query("relation", pair.left.name, pair.right.name)
            dims = (pair.left, pair.right)
            bindings = {
                "paired_scores": tuple(
                    self._binding(
                        o.reference,
                        tuple(
                            e
                            for e in o.reference.evidence_refs
                            if e.kind is self.Kind.SCORE
                            and e.dimension in dims
                            and e.source_id == o.reference.source_id
                        ),
                    )
                    for o in pair.observations
                )
            }
            claims.append(
                Claim(
                    f"{run_id}:diagnostic-{i}",
                    run_id,
                    case.batch_id,
                    case.snapshot_id,
                    self.version,
                    query,
                    self._relation(pair),
                    bindings,
                )
            )
        return CandidateRun(
            run_id,
            "completed",
            ledger=tuple(claims),
            selected_ids=selected,
            compute_seconds=elapsed,
            engine_invocations=1,
            operation_counts=operations,
            engine_qualified_count=result.qualified_count,
            engine_returned_count=len(result.findings),
            search_attempts=len(result.search_audit),
        )
