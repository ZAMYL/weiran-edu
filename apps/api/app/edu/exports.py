"""Private, bounded exports from saved server facts and current approved text."""

import csv
import io
import json
from collections import Counter
from zipfile import ZIP_DEFLATED, ZipFile

from app.core.errors import APIError

from .analysis import DIMENSION_MAP
from .auth import Principal
from .results import ResultService
from .reviews import version_conflict
from .service import fail, stamp
from .text_projection import published_text

MAX_EXPORT_BYTES = 8 * 1024 * 1024


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
    def export(
        self,
        principal: Principal,
        batch_id: str,
        result_id: str,
        *,
        snapshot_id: str,
        expected_version: int,
        format: str,
    ) -> tuple[bytes, str, str]:
        with self.database.transaction() as connection:
            batch = self._batch(connection, principal, batch_id, snapshot_id)
            result = self._dto(*self._rows(connection, batch_id, result_id))
            if result.version != expected_version:
                version_conflict("ANALYSIS_RESULT_VERSION_MISMATCH")
            wanted = {
                score.evidence_id for member in result.analysis.members for score in member.scores
            }
            evidence, text_size = [], 0
            for row in connection.execute(self._evidence_rows(), (batch_id,)):
                if row["evidence_id"] not in wanted:
                    continue
                proof = self._display_evidence(connection, row).model_dump(mode="json")
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
            counts = Counter(f.teacher_review.status for f in result.findings)
            payload = {
                "format_version": "edu.private-export.v1",
                "audience": "authorized_teacher_only",
                "public_display_allowed": False,
                "exported_at": stamp(self.clock()),
                "result": result.model_dump(mode="json", exclude={"analysis", "findings"}),
                "analysis": result.analysis.model_dump(mode="json", exclude={"candidates"}),
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
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            if len(content) > MAX_EXPORT_BYTES:
                raise APIError(413, "EXPORT_TOO_LARGE", "导出内容超过当前同步限制")
        filename = f"weiran-analysis-{result.result_id}-v{result.version}"
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
