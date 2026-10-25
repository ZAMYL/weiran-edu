"""Real loopback HTTP acceptance against an operator-started FastAPI; no model calls."""

import argparse
import getpass
import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from edu_demo_fixtures import (
    CODES,
    DIMENSIONS,
    Fixture,
    fixture_arguments,
    make_fixture,
    safe_directory,
    write_new,
)

PREFIX = "/api/v1/edu"
A, S = CODES[0], CODES[4]
PAGE_SIZE = 7


class CheckFailure(Exception):
    pass


def check(condition: bool, code: str = "CHECK_FAILED") -> None:
    if not condition:
        raise CheckFailure(code)


def local_url(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "::1"}
        or url.username
        or url.password
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ValueError("LOOPBACK_URL_REQUIRED")
    _ = url.port  # Reject malformed ports before credentials can be sent.
    return value.rstrip("/")


def exact_value(body: dict, expected: Decimal | Fraction | int) -> None:
    """Compare exact rational, numeric value and HALF_UP display independently."""
    value = Fraction(expected)
    check(body["exact"] == {"numerator": value.numerator, "denominator": value.denominator})
    decimal = Decimal(value.numerator) / Decimal(value.denominator)
    check(abs(Decimal(str(body["value"])) - decimal) < Decimal("0.000000000001"))
    check(body["display"] == str(decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))


def dimension_oracle(fixture: Fixture, code: str) -> dict:
    values = sorted(row[CODES.index(code)] for row in fixture.scores)
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    # Convert the Decimal sum to Fraction before division; never round a recurring mean.
    return {
        "mean": Fraction(sum(values)) / len(values),
        "median": median,
        "minimum": values[0],
        "maximum": values[-1],
        "frequencies": Counter(values),
    }


def conditions(operator: str = "lte") -> list[dict]:
    return [
        {"dimension_code": A, "operator": operator, "value": 2.75},
        {"dimension_code": S, "operator": "gte", "value": 4},
    ]


def selected_indexes(fixture: Fixture, rules: list[dict]) -> list[int]:
    comparisons = {
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
        "eq": lambda a, b: a == b,
        "gt": lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
    }
    return [
        index
        for index, row in enumerate(fixture.scores)
        if all(
            comparisons[rule["operator"]](
                row[CODES.index(rule["dimension_code"])], Decimal(str(rule["value"]))
            )
            for rule in rules
        )
    ]


class Smoke:
    def __init__(
        self, client: httpx.Client, token: str, fixture: Fixture, scope_id: str | None = None
    ) -> None:
        self.client = client
        self._headers = {"Authorization": "Bearer " + token}
        self.fixture = fixture
        self.scope_id = scope_id
        self.steps: list[dict] = []
        self.counts: dict[str, int] = {}
        self._responses: list[dict] = []

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        started = perf_counter()
        self._responses = []
        entry = {"step": name, "passed": False, "failure_code": None}
        try:
            yield
            entry["passed"] = True
        except Exception as exc:
            # No exception messages, response bodies, URLs, IDs or credentials in the summary.
            entry["failure_code"] = (
                str(exc) if isinstance(exc, CheckFailure) else "EXECUTION_FAILED"
            )
            raise
        finally:
            entry["elapsed_ms"] = round((perf_counter() - started) * 1000, 2)
            entry["http_checks"] = self._responses
            self.steps.append(entry)

    def request(
        self,
        method: str,
        path: str,
        *,
        status: int | tuple[int, ...] = 200,
        code: str | None = None,
        authenticated: bool = True,
        **kwargs: object,
    ) -> dict:
        headers = self._headers if authenticated else {}
        response = self.client.request(method, path, headers=headers, **kwargs)
        error_code = None
        body = response.json()
        if response.status_code >= 400:
            error = body.get("error", {})
            raw = error.get("code", "")
            error_code = raw if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", raw) else "INVALID_ERROR_CODE"
            check(error.get("request_id") == response.headers.get("X-Request-ID"))
        self._responses.append({"status": response.status_code, "error_code": error_code})
        check(
            response.status_code in ((status,) if isinstance(status, int) else status),
            "HTTP_STATUS_MISMATCH",
        )
        if code is not None:
            check(error_code == code, "HTTP_ERROR_CODE_MISMATCH")
        if path.startswith(PREFIX):
            check(response.headers.get("cache-control") == "no-store")
            check(response.headers.get("x-request-id", "").startswith("req_"))
        return body

    def upload(self, fixture: Fixture, *, scope: str | None = None, status: int = 201) -> dict:
        return self.request(
            "POST",
            PREFIX + "/imports/preview",
            status=status,
            data={"data_scope_id": scope or self.scope_id, "title": "纯合成 HTTP 验收"},
            files={
                "file": (
                    "synthetic.xlsx",
                    fixture.data,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )

    def pages(
        self, fetch: Callable[[int], dict], total: int, key: str = "items", page_key: str = "page"
    ) -> list[dict]:
        output = []
        for offset in range(0, total + PAGE_SIZE, PAGE_SIZE):
            body = fetch(offset)
            if "total_count" in body:
                check(body["total_count"] == total)
            items = body[key]
            check(len(items) == min(PAGE_SIZE, max(total - offset, 0)))
            check(
                body[page_key]
                == {
                    "offset": offset,
                    "limit": PAGE_SIZE,
                    "returned": len(items),
                    "has_more": offset + len(items) < total,
                }
            )
            output.extend(items)
        return output

    @staticmethod
    def binding(body: dict, batch: dict) -> None:
        check(
            body["batch_id"] == batch["batch_id"] and body["snapshot_id"] == batch["snapshot_id"],
            "SOURCE_OR_SNAPSHOT_MISMATCH",
        )

    def run(self) -> None:
        fixture, n = self.fixture, self.fixture.students
        with self.step("runtime_openapi_and_credentials"):
            schema = self.request("GET", "/openapi.json")
            for suffix, method in (
                ("/me", "get"),
                ("/scopes", "get"),
                ("/imports/preview", "post"),
                ("/batches/{batch_id}/insights/query", "post"),
                ("/batches/{batch_id}/insights/{candidate_id}/records/query", "post"),
            ):
                check(method in schema["paths"].get(PREFIX + suffix, {}), "REQUIRED_API_MISSING")
            self.request(
                "GET", PREFIX + "/me", status=401, code="UNAUTHORIZED", authenticated=False
            )
            check(bool(self.request("GET", PREFIX + "/me")["teacher_id"]), "CREDENTIAL_REJECTED")
            scopes = self.request("GET", PREFIX + "/scopes")["items"]
            available = [s["data_scope_id"] for s in scopes if s["can_import"]]
            if self.scope_id is None:
                check(len(available) == 1, "EXPLICIT_IMPORT_SCOPE_REQUIRED")
                self.scope_id = available[0]
            check(self.scope_id in available, "IMPORT_PERMISSION_REQUIRED")
            self.upload(fixture, scope="synthetic-ungranted-scope", status=403)
            batch_params = {"data_scope_id": self.scope_id, "limit": 1}
            before_batches = self.request("GET", PREFIX + "/batches", params=batch_params)[
                "total_count"
            ]
        with self.step("upload_preview_quality_and_pagination"):
            preview = self.upload(fixture)
            summary = preview["summary"]
            errors, warnings = (
                (2 if fixture.case == "blocked" else 0),
                (3 if fixture.case == "warning" else 0),
            )
            check(
                summary
                == {
                    "detected_student_count": n,
                    "complete_student_count": n - (fixture.case == "blocked"),
                    "parsed_evaluation_count": n * 5,
                    "error_count": errors,
                    "warning_count": warnings,
                }
            )
            info = preview["import"]
            check(info["status"] == ("invalid" if errors else "preview_ready"))
            check(info["file_sha256"] == hashlib.sha256(fixture.data).hexdigest())
            imp = PREFIX + "/imports/" + info["import_id"]
            report = self.request("GET", imp + "/quality-report")
            check(
                report["status"]
                == {"valid": "passed", "warning": "warning", "blocked": "blocked"}[fixture.case]
            )
            check(report["requires_warning_acknowledgement"] == bool(warnings))
            expected_codes = (
                {"COMMENT_EMPTY", "COMMENT_REPEATED"}
                if warnings
                else {"SCORE_OUT_OF_RANGE", "SCORE_PRECISION"}
                if errors
                else set()
            )
            check({issue["code"] for issue in report["issues"]} == expected_codes)
            check(len(report["issues"]) == errors + warnings)
            rows = self.pages(
                lambda offset: self.request(
                    "GET", imp, params={"row_offset": offset, "row_limit": PAGE_SIZE}
                ),
                n * 5,
                "rows",
                "row_page",
            )
            for index, row in enumerate(rows):
                student, dimension = divmod(index, 5)
                expected = fixture.scores[student][dimension]
                if not (fixture.case == "blocked" and index in (0, 1)):
                    check(Decimal(str(row["score"])) == expected)
                else:
                    check(row["score"] is None and row["score_state"] == "invalid")
                check(row["comment"] is None)
                check(row["source"]["file_sha256"] == info["file_sha256"])
            samples = self.pages(
                lambda offset: self.request(
                    "GET", imp, params={"student_offset": offset, "student_limit": PAGE_SIZE}
                ),
                n - bool(errors),
                "student_samples",
                "student_page",
            )
            check(len({s["preview_student_id"] for s in samples}) == len(samples))
            self.counts.update(
                students=n, score_rows=n * 5, quality_errors=errors, quality_warnings=warnings
            )
        with self.step("confirmation_gates_and_commit"):
            version = info["preview_version"]
            wrong_version = ("0" if version[0] != "0" else "1") + version[1:]
            self.request(
                "POST",
                imp + "/commit",
                status=409,
                code="PREVIEW_VERSION_MISMATCH",
                json={"preview_version": wrong_version},
            )
            if errors:
                self.request(
                    "POST",
                    imp + "/commit",
                    status=409,
                    code="IMPORT_NOT_CONFIRMABLE",
                    json={"preview_version": version, "acknowledge_warnings": True},
                )
                check(
                    self.request("GET", PREFIX + "/batches", params=batch_params)["total_count"]
                    == before_batches
                )
                return
            if warnings:
                self.request(
                    "POST",
                    imp + "/commit",
                    status=409,
                    code="WARNINGS_NOT_ACKNOWLEDGED",
                    json={"preview_version": version},
                )
            result = self.request(
                "POST",
                imp + "/commit",
                status=(200, 201),
                json={"preview_version": version, "acknowledge_warnings": bool(warnings)},
            )
            check(result["result"] in ("created", "duplicate"))
            self.counts["duplicate_batch_reused"] = int(result["result"] == "duplicate")
            check(
                self.request("GET", PREFIX + "/batches", params=batch_params)["total_count"]
                == before_batches + (result["result"] == "created")
            )
            replay = self.request("POST", imp + "/commit", json={"preview_version": version})
            check(replay["batch_id"] == result["batch_id"] and replay["result"] == "replayed")
            base = PREFIX + "/batches/" + result["batch_id"]
            batch = self.request("GET", base)
            check(batch["student_count"] == n and batch["evaluation_count"] == n * 5)
            check(batch["content_sha256"] == info["content_sha256"])
            if result["result"] == "created":
                check(batch["file_sha256"] == info["file_sha256"])
            snapshot = {"snapshot_id": batch["snapshot_id"]}

        def get(suffix: str, **params: object) -> dict:
            body = self.request("GET", base + suffix, params={**snapshot, **params})
            self.binding(body, batch)
            return body

        with self.step("anonymous_scores_evidence_and_rubric"):
            students = self.pages(
                lambda offset: get("/students", offset=offset, limit=PAGE_SIZE), n
            )
            check(len({s["student_id"] for s in students}) == n)
            rubric = get("/rubric")
            check(len(rubric["criteria"]) == 5)
            criteria = {c["dimension_code"]: c for c in rubric["criteria"]}
            for spec in DIMENSIONS:
                criterion = criteria[spec.code]
                check(
                    criterion["criteria_text"] is None
                    and criterion["criteria_status"] == "withheld"
                )
                check(
                    criterion["source"]
                    == {
                        "file_sha256": batch["file_sha256"],
                        "sheet_name": "Sheet1",
                        "range": spec.rubric_range,
                    }
                )
            for index, student in enumerate(students):
                check({s["dimension_code"] for s in student["scores"]} == set(CODES))
                proofs = get("/students/" + student["student_id"] + "/evidence")["items"]
                check(len(proofs) == 5)
                for score in student["scores"]:
                    dimension = CODES.index(score["dimension_code"])
                    check(Decimal(str(score["score"])) == fixture.scores[index][dimension])
                    proof = next(p for p in proofs if p["evidence_id"] == score["evidence_id"])
                    self.binding(proof, batch)
                    check(proof["student_id"] == student["student_id"])
                    check(
                        proof["dimension_code"] == CODES[dimension]
                        and proof["score"] == score["score"]
                    )
                    row = 3 + index * 6 + dimension
                    check(
                        proof["source"]
                        == {
                            "file_sha256": batch["file_sha256"],
                            "sheet_name": "Sheet2",
                            "row_number": row,
                            "dimension_cell": f"B{row}",
                            "score_cell": f"C{row}",
                            "comment_cell": f"D{row}",
                        }
                    )
                    check(
                        proof["rubric_criterion_id"]
                        == criteria[CODES[dimension]]["rubric_criterion_id"]
                    )
                    check(proof["comment"] is None)
                    check(Decimal(proof["score_raw"]) == fixture.scores[index][dimension])
                    if index == 0:
                        check(get("/evidence/" + score["evidence_id"]) == proof)
            self.counts["verified_evidence_rows"] = n * 5
        with self.step("independent_decimal_statistics"):
            stats = get("/statistics")
            check(stats["scope"] == {"kind": "batch", "record_count": n})
            check({d["dimension_code"] for d in stats["dimensions"]} == set(CODES))
            for dimension in stats["dimensions"]:
                oracle = dimension_oracle(fixture, dimension["dimension_code"])
                check(
                    (
                        dimension["valid_count"],
                        dimension["missing_count"],
                        dimension["invalid_count"],
                    )
                    == (n, 0, 0)
                )
                for key in ("mean", "median", "minimum", "maximum"):
                    exact_value(dimension[key], oracle[key])
                check(len(dimension["distribution"]) == len(oracle["frequencies"]))
                for frequency in dimension["distribution"]:
                    count = oracle["frequencies"][Decimal(str(frequency["score"]))]
                    check(frequency["count"] == count and frequency["denominator"] == n)
                    exact_value(frequency["ratio"], Fraction(count, n))

        def post(suffix: str, body: dict) -> dict:
            result = self.request("POST", base + suffix, json={**snapshot, **body})
            self.binding(result, batch)
            return result

        with self.step("and_boundaries_zero_matches_and_pagination"):
            for operator in ("lt", "lte", "eq"):
                rules = conditions(operator)
                indexes = selected_indexes(fixture, rules)

                def fetch(
                    offset: int, rules: list[dict] = rules, indexes: list[int] = indexes
                ) -> dict:
                    result = post(
                        "/groups/query", {"conditions": rules, "offset": offset, "limit": PAGE_SIZE}
                    )
                    check(result["matched_count"] == len(indexes) and result["eligible_count"] == n)
                    check(result["total_count"] == n and result["unevaluable_count"] == 0)
                    check(abs(result["ratio"] - len(indexes) / n) < 1e-12)
                    check(abs(result["matched_among_eligible_ratio"] - len(indexes) / n) < 1e-12)
                    check(result["conditions"] == rules)
                    # Group total_count is the batch denominator, not the matched pagination total.
                    return {**result, "total_count": len(indexes)}

                members = self.pages(fetch, len(indexes))
                check(members == [students[index] for index in indexes])
                self.counts["and_" + operator + "_matches"] = len(indexes)
            zero_rules = [{"dimension_code": A, "operator": "gt", "value": 5}]
            zero = post("/groups/query", {"conditions": zero_rules})
            check(zero["items"] == [] and zero["matched_count"] == 0 and zero["total_count"] == n)
        with self.step("formal_insights_and_metric_sources"):
            rules = conditions()
            context = {"conditions": rules, "include_dimensions": True}
            indexes = selected_indexes(fixture, rules)
            cards = self.pages(
                lambda offset: post(
                    "/insights/query", {**context, "offset": offset, "limit": PAGE_SIZE}
                ),
                5 + bool(indexes),
            )
            check(len({c["candidate_id"] for c in cards}) == len(cards))
            check({c["dimension_code"] for c in cards if c["dimension_code"]} == set(CODES))
            for card in cards:
                self.binding(card, batch)
                check(card["denominator"] == n and card["public_display_allowed"] is False)
                code = card["dimension_code"]
                oracle = dimension_oracle(fixture, code) if code else None
                metrics = {m["key"]: m for m in card["metrics"]}
                expected_keys = (
                    {"valid_count", "missing_count", "invalid_count", "mean", "median"}
                    | {"frequency:" + str(Fraction(v)) for v in oracle["frequencies"]}
                    if code
                    else {
                        "matched_count",
                        "matched_ratio",
                        "eligible_count",
                        "matched_among_eligible",
                        "unevaluable_count",
                    }
                )
                check(set(metrics) == expected_keys and len(metrics) == len(card["metrics"]))
                for key, metric in metrics.items():
                    check(metric["denominator"] == n)
                    if code:
                        if key in ("mean", "median"):
                            expected, sources = oracle[key], list(range(n))
                        elif key.startswith("frequency:"):
                            value = Fraction(key.split(":", 1)[1])
                            sources = [
                                i
                                for i, row in enumerate(fixture.scores)
                                if Fraction(row[CODES.index(code)]) == value
                            ]
                            expected = len(sources)
                            exact_value(metric["at_score"], value)
                        else:
                            check(
                                key in ("valid_count", "missing_count", "invalid_count"),
                                "UNKNOWN_METRIC",
                            )
                            expected = n if key == "valid_count" else 0
                            sources = list(range(n)) if expected else []
                    else:
                        check(
                            key
                            in (
                                "matched_count",
                                "matched_ratio",
                                "eligible_count",
                                "matched_among_eligible",
                                "unevaluable_count",
                            ),
                            "UNKNOWN_METRIC",
                        )
                        sources = (
                            list(range(n))
                            if key == "eligible_count"
                            else []
                            if key == "unevaluable_count"
                            else indexes
                        )
                        expected = (
                            Fraction(len(indexes), n)
                            if key in ("matched_ratio", "matched_among_eligible")
                            else len(sources)
                        )
                    exact_value(metric["value"], expected)
                    check(metric["source_record_count"] == len(sources))
                    # Paginate means, boundary frequency, empty sources and all group metrics.
                    if (
                        not code
                        or key == "mean"
                        or (code == A and key in ("frequency:11/4", "missing_count"))
                    ):

                        def fetch_records(
                            offset: int, card: dict = card, key: str = key, metric: dict = metric
                        ) -> dict:
                            result = post(
                                "/insights/" + card["candidate_id"] + "/records/query",
                                {
                                    **context,
                                    "metric_key": key,
                                    "offset": offset,
                                    "limit": PAGE_SIZE,
                                },
                            )
                            check(
                                result["candidate_id"] == card["candidate_id"]
                                and result["metric"] == metric
                            )
                            check(result["scope_record_count"] == n)
                            return result

                        records = self.pages(fetch_records, len(sources))
                        required_codes = {code} if code else {A, S}
                        expected_records = [
                            {
                                **students[i],
                                "scores": [
                                    s
                                    for s in students[i]["scores"]
                                    if s["dimension_code"] in required_codes
                                ],
                            }
                            for i in sources
                        ]
                        check(records == expected_records, "METRIC_SOURCE_MISMATCH")
            empty = post("/insights/query", {"conditions": zero_rules, "include_dimensions": False})
            check(empty["total_count"] == 0 and empty["items"] == [])
            check(empty["comment_analysis_status"] == "unavailable")
            self.counts["insight_candidates"] = len(cards)
        with self.step("snapshot_validation_and_cross_batch_ownership"):
            stale = {"snapshot_id": "synthetic-stale-snapshot"}
            for suffix in (
                "/statistics",
                "/students",
                "/rubric",
                "/evidence/" + students[0]["scores"][0]["evidence_id"],
            ):
                self.request(
                    "GET", base + suffix, params=stale, status=409, code="SNAPSHOT_MISMATCH"
                )
            for suffix, payload in (
                ("/groups/query", {"conditions": rules}),
                ("/insights/query", context),
                (
                    "/insights/" + cards[0]["candidate_id"] + "/records/query",
                    {**context, "metric_key": "mean"},
                ),
            ):
                self.request(
                    "POST",
                    base + suffix,
                    json={**stale, **payload},
                    status=409,
                    code="SNAPSHOT_MISMATCH",
                )
            self.request(
                "POST",
                base + "/groups/query",
                status=422,
                json={
                    **snapshot,
                    "conditions": [{"dimension_code": A, "operator": "lt", "value": 2.751}],
                },
            )
            self.request(
                "POST", base + "/insights/query", status=422, json={**snapshot, "limit": 0}
            )
            other = self.upload(make_fixture(1, fixture.seed + 1))
            other_commit = self.request(
                "POST",
                PREFIX + "/imports/" + other["import"]["import_id"] + "/commit",
                status=(200, 201),
                json={"preview_version": other["import"]["preview_version"]},
            )
            other_base = PREFIX + "/batches/" + other_commit["batch_id"]
            self.request(
                "GET",
                other_base + "/evidence/" + students[0]["scores"][0]["evidence_id"],
                status=404,
            )
            self.request(
                "POST",
                other_base + "/insights/" + cards[0]["candidate_id"] + "/records/query",
                status=404,
                json={
                    "snapshot_id": self.request("GET", other_base)["snapshot_id"],
                    **context,
                    "metric_key": "mean",
                },
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    fixture_arguments(parser)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scope-id", default=os.environ.get("WEIRAN_EDU_DEMO_SCOPE"))
    args = parser.parse_args(argv)
    started = perf_counter()
    smoke = None
    failure = None
    try:
        safe_directory(args.output_dir)
        url = local_url(args.base_url)
        fixture = make_fixture(args.students, args.seed, args.case)
        token = os.environ.get("WEIRAN_EDU_DEMO_TOKEN") or getpass.getpass(
            "Local pilot token (hidden): "
        )
        check(bool(token), "TOKEN_REQUIRED")
        with httpx.Client(
            base_url=url, timeout=60, trust_env=False, follow_redirects=False
        ) as client:
            smoke = Smoke(client, token, fixture, args.scope_id)
            smoke.run()
    except Exception as exc:
        failure = str(exc) if isinstance(exc, CheckFailure) else "EXECUTION_FAILED"
    summary = {
        "schema": "edu.demo.smoke.v1",
        "passed": failure is None,
        "case": args.case,
        "students": args.students,
        "seed": args.seed,
        "flow": (
            "incomplete"
            if failure
            else "blocked_rejected"
            if args.case == "blocked"
            else "confirmed_batch"
        ),
        "failure_code": failure,
        "counts": smoke.counts if smoke else {},
        "steps": smoke.steps if smoke else [],
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
    }
    try:
        write_new(
            args.output_dir,
            "v1-smoke-summary-" + uuid4().hex + ".json",
            json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    except (OSError, ValueError):
        summary["passed"], summary["failure_code"] = False, "SUMMARY_OUTPUT_FAILED"
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
