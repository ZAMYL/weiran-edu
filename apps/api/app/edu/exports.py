"""Private, bounded exports from saved server facts and current approved text."""

import asyncio
import csv
import io
import json
import sqlite3
from collections import Counter
from zipfile import ZIP_DEFLATED, ZipFile

from app.core.errors import APIError
from app.reporting.edu import ExportLimits, ReportError, generate_report

from .analysis import DIMENSION_MAP
from .auth import Principal
from .discovery_results import DiscoveryResultService
from .report_exports import report_input
from .results import ResultService
from .review_models import AnalysisResult
from .reviews import version_conflict
from .service import fail, stamp
from .support_models import ExportCheck, ExportFormat, ExportWarning
from .text_projection import published_text

MAX_EXPORT_BYTES = 8 * 1024 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
REPORT_FORMATS = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
}


def csv_bytes(rows: list[list[object]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    for row in rows:
        safe = []
        for value in row:
            text = "" if value is None else str(value)
            if text.lstrip()[:1] in {"=", "+", "-", "@"} or text[:1] in {"\t", "\r", "\n"}:
                text = "'" + text
            safe.append(text)
        writer.writerow(safe)
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


class ResultExporter(ResultService):
    def _current(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        batch_id: str,
        result_id: str,
        snapshot_id: str,
        expected_version: int,
    ) -> tuple[sqlite3.Row, AnalysisResult]:
        batch = self._batch(connection, principal, batch_id, snapshot_id)
        result = self._dto(*self._rows(connection, batch_id, result_id))
        if result.version != expected_version:
            version_conflict("ANALYSIS_RESULT_VERSION_MISMATCH")
        if result.snapshot_id != snapshot_id:
            fail("DATA_INTEGRITY_ERROR", 503)
        return batch, result

    def _download_input(
        self,
        principal: Principal,
        batch_id: str,
        result_id: str,
        snapshot_id: str,
        expected_version: int,
        format: ExportFormat,
    ) -> tuple[AnalysisResult, dict, bytes, bytes | None]:
        try:
            with self.database.transaction() as connection:
                result, payload, content = self._prepare(
                    connection, principal, batch_id, result_id, snapshot_id, expected_version
                )
                report = None
                if format in REPORT_FORMATS:
                    batch = self._batch(connection, principal, batch_id, snapshot_id)
                    report = report_input(
                        connection, batch, result, payload["rubric"], generated_at=self.clock()
                    )
            if result.analysis.discovery is not None:
                # No SQLite read lock while the bounded worker recomputes selected facts.
                asyncio.run(
                    DiscoveryResultService(self.database, clock=self.clock).verify_saved(
                        principal, result
                    )
                )
            report_content = None
            if report is not None:
                # Build once, completely in memory. Both preflight/download use these guards.
                files = generate_report(
                    report, limits=ExportLimits(max_file_bytes=MAX_REPORT_BYTES)
                )
                report_content = files.xlsx if format == "xlsx" else files.markdown.encode("utf-8")
                if len(report_content) > MAX_REPORT_BYTES:
                    raise APIError(413, "EXPORT_TOO_LARGE", "报告超过当前同步导出限制")
            if report is not None or result.analysis.discovery is not None:
                # Reopen after compute/render so revocation/version cannot reuse the old read.
                with self.database.transaction() as connection:
                    _, current = self._current(
                        connection, principal, batch_id, result_id, snapshot_id, expected_version
                    )
                    if current.analysis != result.analysis:
                        fail("DATA_INTEGRITY_ERROR", 503)
                    if current.analysis.discovery is not None:
                        # The discovery recomputation can outlive a text publication.
                        # Refresh approved text under this final transaction; never ship
                        # a comment/rubric withdrawn while the child was calculating.
                        _, payload, content = self._prepare(
                            connection,
                            principal,
                            batch_id,
                            result_id,
                            snapshot_id,
                            expected_version,
                        )
            return result, payload, content, report_content
        except ReportError as error:
            if str(error) in {
                "report_file_size_limit",
                "report_layout_limit",
                "member_limit",
                "dimension_limit",
                "distribution_limit",
            }:
                raise APIError(413, "EXPORT_TOO_LARGE", "报告超过当前同步导出限制") from None
            fail("DATA_INTEGRITY_ERROR", 503)

    def _prepare(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        batch_id: str,
        result_id: str,
        snapshot_id: str,
        expected_version: int,
    ) -> tuple[AnalysisResult, dict, bytes]:
        batch, result = self._current(
            connection, principal, batch_id, result_id, snapshot_id, expected_version
        )
        members = result.analysis.members
        wanted = {
            score.evidence_id: (member.student_id, score.dimension_code, score.score)
            for member in members
            for score in member.scores
        }
        if (
            len({m.student_id for m in members}) != len(members)
            or len(members) != result.analysis.matched_count
            or not 0 <= len(members) <= result.analysis.total_count == batch["student_count"]
            or len(wanted) != len(members) * 5
            or any(
                len(m.scores) != 5 or {s.dimension_code for s in m.scores} != set(DIMENSION_MAP)
                for m in members
            )
        ):
            fail("DATA_INTEGRITY_ERROR", 503)
        evidence, text_size = [], 0
        for row in connection.execute(self._evidence_rows(), (batch_id,)):
            if row["evidence_id"] not in wanted:
                continue
            proof = self._display_evidence(connection, row).model_dump(mode="json")
            if (proof["student_id"], proof["dimension_code"], proof["score"]) != wanted[
                row["evidence_id"]
            ] or proof["snapshot_id"] != snapshot_id:
                fail("DATA_INTEGRITY_ERROR", 503)
            text_size += len(json.dumps(proof, ensure_ascii=False).encode())
            if text_size > MAX_EXPORT_BYTES:
                raise APIError(413, "EXPORT_TOO_LARGE", "导出内容超过当前同步限制")
            evidence.append(proof)
        if len(evidence) != len(wanted):
            fail("DATA_INTEGRITY_ERROR", 503)
        criteria = []
        for row in connection.execute(
            "SELECT rubric_criterion_id,dimension_code,file_sha256,sheet_name,source_range "
            "FROM rubric_criteria WHERE rubric_id=? ORDER BY dimension_code",
            (batch["rubric_id"],),
        ):
            text, status, version = published_text(
                connection, batch_id, "rubric", row["rubric_criterion_id"]
            )
            criteria.append(
                {
                    **dict(row),
                    "criteria_text": text,
                    "criteria_status": status,
                    "criteria_review_version": version,
                }
            )
        all_findings = (*result.findings, *result.discovery_findings)
        counts = Counter(f.teacher_review.status for f in all_findings)
        payload = {
            "format_version": "edu.private-export.v1",
            "audience": "authorized_teacher_only",
            "public_display_allowed": False,
            "exported_at": stamp(self.clock()),
            "result": result.model_dump(
                mode="json", exclude={"analysis", "findings", "discovery_findings"}
            ),
            "analysis": result.analysis.model_dump(
                mode="json", exclude={"candidates", "discovery"}
            ),
            "confirmed_findings": [
                f.model_dump(mode="json")
                for f in result.findings
                if f.teacher_review.status == "confirmed"
            ],
            "candidate_status_counts": {
                key: counts[key] for key in ("pending", "confirmed", "dismissed")
            },
            "evidence": evidence,
            "rubric": criteria,
            "text_policy": (
                "正文按导出时最新审核版本读取；withheld 不等于空原文。教师判断不构成因果证明。"
            ),
        }
        if result.analysis.discovery is not None:
            payload["discovery_spec"] = result.analysis.discovery.spec.model_dump(mode="json")
            payload["confirmed_discoveries"] = [
                f.model_dump(mode="json")
                for f in result.discovery_findings
                if f.teacher_review.status == "confirmed"
            ]
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(content) > MAX_EXPORT_BYTES:
            raise APIError(413, "EXPORT_TOO_LARGE", "导出内容超过当前同步限制")
        return result, payload, content

    def check(
        self,
        principal: Principal,
        batch_id: str,
        result_id: str,
        *,
        snapshot_id: str,
        expected_version: int,
        format: ExportFormat,
    ) -> ExportCheck:
        result, payload, content, report_content = self._download_input(
            principal, batch_id, result_id, snapshot_id, expected_version, format
        )
        warnings = []
        for code, count, message in (
            (
                "PENDING_FINDINGS_EXCLUDED",
                payload["candidate_status_counts"]["pending"],
                "尚未核验的发现不会作为已确认发现导出",
            ),
            (
                "DISMISSED_FINDINGS_EXCLUDED",
                payload["candidate_status_counts"]["dismissed"],
                "已否决的发现不会作为已确认发现导出",
            ),
            (
                "WITHHELD_COMMENTS_EXCLUDED",
                sum(p["comment_status"] != "published" for p in payload["evidence"]),
                "未发布或已撤回的评语正文不导出",
            ),
            (
                "WITHHELD_RUBRIC_EXCLUDED",
                sum(p["criteria_status"] != "published" for p in payload["rubric"]),
                "未发布或已撤回的评分依据正文不导出",
            ),
        ):
            if count:
                warnings.append(ExportWarning(code=code, count=count, message=message))
        report_fields = (
            {}
            if report_content is None
            else {
                "report_payload_bytes": len(report_content),
                "max_report_payload_bytes": MAX_REPORT_BYTES,
                "note": "预检在内存完整生成报告并测量所选格式，不保存文件或锁定状态；"
                "实际下载仍复核权限及核验版本。仅汇总，不导出匿名逐人明细。",
            }
        )
        return ExportCheck(
            batch_id=batch_id,
            snapshot_id=snapshot_id,
            result_id=result_id,
            result_version=result.version,
            format=format,
            member_count=len(result.analysis.members),
            confirmed_finding_count=len(payload["confirmed_findings"])
            + len(payload.get("confirmed_discoveries", [])),
            json_payload_bytes=len(content),
            max_json_payload_bytes=MAX_EXPORT_BYTES,
            warnings=tuple(warnings),
            **report_fields,
        )

    def export(
        self,
        principal: Principal,
        batch_id: str,
        result_id: str,
        *,
        snapshot_id: str,
        expected_version: int,
        format: ExportFormat,
    ) -> tuple[bytes, str, str]:
        result, payload, content, report_content = self._download_input(
            principal, batch_id, result_id, snapshot_id, expected_version, format
        )
        evidence = payload["evidence"]
        filename = f"weiran-analysis-{result.result_id}-v{result.version}"
        if report_content is not None:
            return report_content, REPORT_FORMATS[format], filename + "." + format
        if format == "json":
            return content, "application/json", filename + ".json"
        if format != "zip":
            raise APIError(422, "VALIDATION_ERROR", "导出格式不符合要求")
        students = [
            [
                "student_id",
                "display_label",
                *(column for code in DIMENSION_MAP for column in (code, code + "_evidence_id")),
            ]
        ]
        for member in result.analysis.members:
            scores = {score.dimension_code: score for score in member.scores}
            students.append(
                [
                    member.student_id,
                    member.display_label,
                    *(
                        value
                        for code in DIMENSION_MAP
                        for value in (scores[code].score, scores[code].evidence_id)
                    ),
                ]
            )
        statistics = [
            [
                "scope",
                "dimension_code",
                "valid_count",
                "mean",
                "mean_exact",
                "median",
                "minimum",
                "maximum",
            ]
        ]
        for scope in (result.analysis.statistics, result.analysis.group_statistics):
            if scope is None:
                continue
            for dimension in scope.dimensions:
                exact = dimension.mean.exact
                statistics.append(
                    [
                        scope.scope.kind,
                        dimension.dimension_code,
                        dimension.valid_count,
                        dimension.mean.display,
                        f"{exact.numerator}/{exact.denominator}" if exact else "",
                        dimension.median.display,
                        dimension.minimum.display,
                        dimension.maximum.display,
                    ]
                )
        findings = [
            [
                "candidate_id",
                "title",
                "summary",
                "denominator",
                "rule_version",
                "teacher_status",
                "teacher_note",
            ]
        ]
        for finding in result.findings:
            if finding.teacher_review.status == "confirmed":
                card = finding.candidate
                findings.append(
                    [
                        card.candidate_id,
                        card.title,
                        card.summary,
                        card.denominator,
                        card.rule_version,
                        "confirmed",
                        finding.teacher_review.note,
                    ]
                )
        for finding in result.discovery_findings:
            if finding.teacher_review.status == "confirmed":
                card = finding.discovery
                findings.append(
                    [
                        card.discovery_id,
                        card.title,
                        card.summary,
                        card.scope.scope_record_count,
                        card.rule_version,
                        "confirmed",
                        finding.teacher_review.note,
                    ]
                )
        proof_rows = [
            [
                "evidence_id",
                "student_id",
                "dimension_code",
                "score",
                "comment_status",
                "comment_review_version",
                "reviewed_comment",
                "score_cell",
                "rubric_criterion_id",
            ]
        ]
        proof_rows.extend(
            [
                [
                    p["evidence_id"],
                    p["student_id"],
                    p["dimension_code"],
                    p["score"],
                    p["comment_status"],
                    p["comment_review_version"],
                    p["comment"],
                    p["source"]["score_cell"],
                    p["rubric_criterion_id"],
                ]
                for p in evidence
            ]
        )
        output = io.BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            archive.writestr("analysis.json", content)
            for name, rows in (
                ("students.csv", students),
                ("statistics.csv", statistics),
                ("confirmed-findings.csv", findings),
                ("evidence.csv", proof_rows),
            ):
                archive.writestr(name, csv_bytes(rows))
            archive.writestr(
                "README.txt",
                (
                    "未然 EDU：仅授权教师内部使用，不代表可公开发布。\n"
                    "analysis.json 保存完整统计、精确值、筛选条件、版本与审核来源。\n"
                    "students.csv 一人一行，包含完整匹配群体和五维评分，分页不改变人数。\n"
                    "confirmed-findings.csv 仅含教师已确认候选。\n"
                    "评语和依据采用导出时最新发布文本；未发布/已撤回原文不导出。\n"
                    "CSV 对公式起始字符增加单引号，避免作为电子表格公式执行。\n"
                ).encode(),
            )
        return output.getvalue(), "application/zip", filename + ".zip"
