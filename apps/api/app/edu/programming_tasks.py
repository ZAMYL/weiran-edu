"""B-04C：编程测评错误调查任务的正式入口。

与旧五维任务（挂 batches、结果为五维画像）共用同一套「状态机 + 授权 + 幂等 + 审计」
语义，但来源是 assessments：任务绑定测评、教师、数据范围、来源快照与受控任务 ID，
不再伪造 batch_id，也不复用五维结果契约。

本轮只开放「受控执行器」路径（测试注入）；真实 Qwen Live 由 Codex 接线后开放，
未获批准时稳定拒绝，不产生账本消耗与成功结果。
"""

import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from app.core.errors import APIError
from app.edu.auth import Principal, require_teacher
from app.edu.models import Identifier, Model
from app.edu.originals import OriginalStore
from app.storage.database import Database

PURPOSE_MOCK = "assessment_mock"
PURPOSE_LIVE = "assessment_live"
PURPOSES = frozenset({PURPOSE_MOCK, PURPOSE_LIVE})

CAP_MODEL_REQUESTS = 6
CAP_TOOL_ATTEMPTS = 5
MAX_TIMEOUT = 120
MIN_TIMEOUT = 1

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "timed_out", "revoked"})


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: object) -> str:
    return sha256(encode(value).encode()).hexdigest()


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def blocked(code: str, status: int = 409, message: str = "任务授权、来源或状态不满足") -> None:
    raise APIError(status, code, message)


class ProgrammerTaskCreate(Model):
    """浏览器只能提交这两项；来源指纹、模型、工具与凭证一律由服务端绑定。"""

    purpose: Literal["programming_error_investigation"] = "programming_error_investigation"
    idempotency_key: Identifier
    timeout_seconds: int = 120


class FindingEvidence(Model):
    asset_id: Identifier
    source_entry: Identifier
    student_key: Identifier
    problem_label: Identifier
    source_index: int | None = None
    source_row: int | None = None
    test_index: int | None = None


class ProgrammingFinding(Model):
    finding_id: Identifier
    code: Identifier
    statement: str
    evidence: list[FindingEvidence]


class ProgrammingResult(Model):
    assessment_id: Identifier
    source_snapshot_id: Identifier
    asset_sha256: list[str]
    statement: str
    findings: list[ProgrammingFinding]


class ProgrammingTaskView(Model):
    task_id: str
    assessment_id: str
    source_snapshot_id: str
    purpose: Literal["programming_error_investigation"]
    state: str
    error_code: str | None
    created_at: str
    deadline_at: str
    expires_at: str
    execution_origin: Literal["http_mock", "qwen_live", "none"]
    real_qwen: Literal["not_run", "completed", "failed"]
    production_enabled: bool
    tool_attempts: int
    model_requests: int
    result: ProgrammingResult | None


Runner = Callable[["ProgrammingTaskService", str, dict, Principal, Callable[[], bool]], object]


class ProgrammingTaskService:
    """一个测评一次授权、一个任务一个预算；失败、取消与撤权都不留结果。"""

    def __init__(
        self,
        database: Database,
        *,
        originals: OriginalStore | None = None,
        environment: str = "production",
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.database = database
        self.originals = originals
        self.environment = environment
        self.clock = clock or time.time
        # 服务端测试注入点：只允许在 test 环境赋值，不从请求/环境变量读取。
        self.runner: Runner | None = None

    # ---- 来源身份 ----------------------------------------------------------

    @staticmethod
    def source_fingerprint(connection: sqlite3.Connection, assessment_id: str) -> str | None:
        """来源指纹 = 快照下资产集合（按包序）的哈希；任何资产变化都会改变它。"""
        assets = connection.execute(
            "SELECT asset_id,sha256,entry,storage_state FROM assessment_assets "
            "WHERE assessment_id=? ORDER BY source_index,asset_id",
            (assessment_id,),
        ).fetchall()
        if not assets or any(row["storage_state"] != "stored" for row in assets):
            return None
        return digest([(row["asset_id"], row["sha256"], row["entry"]) for row in assets])

    def _owned(self, connection: sqlite3.Connection, actor: Principal, assessment_id: str) -> dict:
        row = connection.execute(
            "SELECT * FROM assessments WHERE assessment_id=?", (assessment_id,)
        ).fetchone()
        if row is None or row["owner_teacher_id"] != actor.teacher_id:
            blocked("RESOURCE_NOT_FOUND", 404, "测评不存在")
        return dict(row)

    def _purpose(self) -> str:
        """受控执行器只在测试环境可用；其余情况一律视为真实执行，本轮未开放。"""
        if self.environment == "test" and self.runner is not None:
            return PURPOSE_MOCK
        return PURPOSE_LIVE

    def _require_live(self) -> None:
        try:
            from prototypes.durable_teacher_agent.live_admission import LiveAdmission

            LiveAdmission.load()
        except Exception:  # noqa: BLE001 - 任何读取失败都必须封闭拒绝
            blocked(
                "AI_LIVE_ADMISSION_REQUIRED",
                409,
                "编程调查尚未获得独立审核授权与预算，未发起任何请求",
            )
        blocked("AI_LIVE_NOT_ENABLED", 503, "编程调查的真实模型执行由后续 Agent 接线开放")

    # ---- 任务检查 ----------------------------------------------------------

    def _row(self, connection: sqlite3.Connection, task_id: str) -> dict:
        row = connection.execute(
            "SELECT t.*,g.assessment_id,g.snapshot_id,g.source_sha,g.purpose,g.revoked,"
            "g.expires AS grant_expires FROM ai_programming_tasks t "
            "JOIN ai_programming_grants g USING(grant_id) WHERE t.task_id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            blocked("RESOURCE_NOT_FOUND", 404, "任务不存在")
        return dict(row)

    def check(
        self,
        connection: sqlite3.Connection,
        row: dict,
        actor: Principal,
        *,
        running: bool = False,
    ) -> None:
        if row["teacher_id"] != actor.teacher_id:
            blocked("RESOURCE_NOT_FOUND", 404, "任务不存在")
        assessment = self._owned(connection, actor, row["assessment_id"])
        if not assessment["source_snapshot_id"] or (
            assessment["source_snapshot_id"] != row["snapshot_id"]
        ):
            blocked("AI_SOURCE_CONFLICT", 409, "测评来源快照已变化，旧任务不能继续")
        current = self.source_fingerprint(connection, row["assessment_id"])
        if current is None:
            blocked("ORIGINAL_UNAVAILABLE", 409, "来源原件不可用，拒绝以缓存证据代替")
        if current != row["source_sha"]:
            blocked("AI_SOURCE_CONFLICT", 409, "来源资产已变化，旧任务不能继续")
        if row["revoked"] or row["purpose"] not in PURPOSES:
            blocked("AI_PURPOSE_REVOKED", 403, "本测评的调查授权已撤销")
        now = self.clock()
        if min(row["expires"], row["grant_expires"]) <= now:
            blocked("AI_TASK_EXPIRED", 410, "任务或授权已过期")
        if running:
            if row["cancelled"] or row["state"] != "running":
                blocked("AI_TASK_CANCELLED", 409, "任务已取消或不在运行状态")
            if row["deadline"] <= now:
                blocked("AI_TASK_TIMEOUT", 410, "任务已超时")

    def authorize(self, task_id: str, actor: Principal, *, running: bool = False) -> bool:
        with self.database.transaction() as connection:
            self.check(connection, self._row(connection, task_id), actor, running=running)
        return False

    def guard(self, task_id: str) -> bool:
        """模型请求前、工具读取前、结果发布前都必须再走一次。"""
        with self.database.transaction() as connection:
            row = self._row(connection, task_id)
            self.check(
                connection,
                row,
                Principal(row["teacher_id"], row["credential_sha"]),
                running=True,
            )
        return False

    # ---- 创建 --------------------------------------------------------------

    def create(
        self, actor: Principal, assessment_id: str, body: ProgrammerTaskCreate
    ) -> tuple[dict, bool]:
        if not MIN_TIMEOUT <= body.timeout_seconds <= MAX_TIMEOUT:
            raise ValueError("invalid_programming_task_timeout")
        purpose = self._purpose()
        if purpose == PURPOSE_LIVE:
            self._require_live()
        request = body.model_dump(mode="json")
        request_sha = digest(request)
        timeout = body.timeout_seconds
        with self.database.transaction(write=True) as connection:
            require_teacher(connection, actor, stamp(datetime.now(UTC)))
            assessment = self._owned(connection, actor, assessment_id)
            snapshot = assessment["source_snapshot_id"]
            if not snapshot:
                blocked("AI_SOURCE_UNVERIFIED", 409, "测评缺少不可变来源快照，不能绑定调查任务")
            fingerprint = self.source_fingerprint(connection, assessment_id)
            if fingerprint is None:
                blocked("ORIGINAL_UNAVAILABLE", 409, "来源原件不可用，拒绝建立调查任务")

            old = connection.execute(
                "SELECT task_id,request_sha FROM ai_programming_tasks WHERE teacher_id=? "
                "AND idempotency_key=?",
                (actor.teacher_id, body.idempotency_key),
            ).fetchone()
            if old is not None:
                row = self._row(connection, old["task_id"])
                self.check(connection, row, actor)
                if row["assessment_id"] != assessment_id or old["request_sha"] != request_sha:
                    blocked("AI_IDEMPOTENCY_CONFLICT", 409, "同一幂等键对应了不同的请求")
                return row, False

            grant = connection.execute(
                "SELECT * FROM ai_programming_grants WHERE teacher_id=? AND assessment_id=? "
                "AND snapshot_id=?",
                (actor.teacher_id, assessment_id, snapshot),
            ).fetchone()
            if grant is None:
                grant_id = str(uuid4())
                now = self.clock()
                connection.execute(
                    "INSERT INTO ai_programming_grants VALUES (?,?,?,?,?,?,?,0,?)",
                    (
                        grant_id,
                        actor.teacher_id,
                        assessment_id,
                        snapshot,
                        fingerprint,
                        purpose,
                        now + 3600,
                        stamp(datetime.now(UTC)),
                    ),
                )
            else:
                grant_id = grant["grant_id"]
                if grant["source_sha"] != fingerprint:
                    blocked("AI_SOURCE_CONFLICT", 409, "来源资产已变化，旧授权不能继续")
                if grant["revoked"] or grant["expires"] <= self.clock():
                    blocked("AI_PURPOSE_REVOKED", 403, "本测评的调查授权已撤销或过期")
                if connection.execute(
                    "SELECT 1 FROM ai_programming_tasks WHERE grant_id=?", (grant_id,)
                ).fetchone():
                    blocked(
                        "AI_CAMPAIGN_ALREADY_USED",
                        409,
                        "该测评这一版来源已用掉唯一一次调查预算，请勿重复提交",
                    )

            task_id = str(uuid4())
            campaign = "assessment-" + digest((actor.teacher_id, assessment_id, snapshot))[:40]
            now = self.clock()
            connection.execute(
                "INSERT INTO ai_programming_tasks (task_id,grant_id,teacher_id,credential_sha,"
                "idempotency_key,request_sha,request_json,campaign_id,state,created,deadline,"
                "expires,budget_json) VALUES (?,?,?,?,?,?,?,?,'queued',?,?,?,?)",
                (
                    task_id,
                    grant_id,
                    actor.teacher_id,
                    actor.token_hash,
                    body.idempotency_key,
                    request_sha,
                    encode(request),
                    campaign,
                    now,
                    now + timeout,
                    now + 3600,
                    encode(
                        {
                            "campaign_id": campaign,
                            "assessment_id": assessment_id,
                            "source_snapshot_id": snapshot,
                            "request_count": 0,
                            "tool_attempts": 0,
                            "max_requests": CAP_MODEL_REQUESTS,
                            "max_tool_attempts": CAP_TOOL_ATTEMPTS,
                        }
                    ),
                ),
            )
            self.event(connection, task_id, "queued", {"purpose": purpose})
            return self._row(connection, task_id), True

    # ---- 状态与结果 --------------------------------------------------------

    def event(
        self, connection: sqlite3.Connection, task_id: str, phase: str, payload: dict
    ) -> None:
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM ai_programming_events WHERE task_id=?",
            (task_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO ai_programming_events VALUES (?,?,?,?,?)",
            (task_id, sequence, phase, encode(payload), stamp(datetime.now(UTC))),
        )

    def events(self, task_id: str, *, after: int = 0, limit: int = 200) -> list[dict]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT sequence,phase,payload,created_at FROM ai_programming_events "
                "WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (task_id, after, limit),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "phase": row["phase"],
                "at": row["created_at"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def start(self, task_id: str) -> dict:
        with self.database.transaction(write=True) as connection:
            row = self._row(connection, task_id)
            self.check(connection, row, Principal(row["teacher_id"], row["credential_sha"]))
            if row["state"] != "queued" or row["deadline"] <= self.clock():
                blocked("AI_TASK_NOT_RUNNABLE", 409, "任务不可运行")
            connection.execute(
                "UPDATE ai_programming_tasks SET state='running' WHERE task_id=?", (task_id,)
            )
            self.event(connection, task_id, "running", {})
            return self._row(connection, task_id)

    def budget(self, task_id: str, *, request_count: int, tool_attempts: int) -> None:
        """预算单调递增、封顶复用；服务端记账，绝不接受客户端自报。"""
        with self.database.transaction(write=True) as connection:
            row = self._row(connection, task_id)
            self.check(
                connection,
                row,
                Principal(row["teacher_id"], row["credential_sha"]),
                running=True,
            )
            old = json.loads(row["budget_json"])
            if request_count < old["request_count"] or tool_attempts < old["tool_attempts"]:
                blocked("AI_BUDGET_RESET_FORBIDDEN", 409, "预算不允许回退或重放")
            if request_count > CAP_MODEL_REQUESTS:
                blocked("AI_BUDGET_EXHAUSTED", 409, "模型请求预算已用尽")
            if tool_attempts > CAP_TOOL_ATTEMPTS:
                blocked("AI_TOOL_BUDGET_EXHAUSTED", 409, "工具读取预算已用尽")
            old["request_count"] = request_count
            old["tool_attempts"] = tool_attempts
            connection.execute(
                "UPDATE ai_programming_tasks SET budget_json=?,tool_attempts=? WHERE task_id=?",
                (encode(old), tool_attempts, task_id),
            )
            self.event(
                connection,
                task_id,
                "model_budget",
                {"request_count": request_count, "tool_attempts": tool_attempts},
            )

    def execute(self, task_id: str) -> None:
        try:
            row = self.start(task_id)
        except APIError:
            return
        try:
            actor = Principal(row["teacher_id"], row["credential_sha"])
            if row["purpose"] != PURPOSE_MOCK:
                self._require_live()
            if self.runner is None:
                blocked("AI_LIVE_ADMISSION_REQUIRED", 409, "没有可用的受控执行器")
            raw = self.runner(self, task_id, row, actor, lambda: self.guard(task_id))
            result = ProgrammingResult.model_validate(raw)
            self.finish(task_id, actor, row, result)
        except APIError as error:
            self.fail(task_id, error.code)
        except (ValueError, TypeError, KeyError):
            self.fail(task_id, "AI_TASK_RESULT_INVALID")
        except Exception:  # noqa: BLE001 - 任何未预期异常都不发布部分结果
            self.fail(task_id, "AI_TASK_FAILED")

    def finish(self, task_id: str, actor: Principal, row: dict, result: ProgrammingResult) -> None:
        with self.database.transaction(write=True) as connection:
            current = self._row(connection, task_id)
            self.check(connection, current, actor, running=True)
            self.verify_originals(connection, current)
            self.verify_result(connection, current, result)
            raw = result.model_dump(mode="json")
            connection.execute(
                "UPDATE ai_programming_tasks SET state='completed',result_json=?,result_sha=?,"
                "error_code=NULL WHERE task_id=?",
                (encode(raw), digest(raw), task_id),
            )
            self.event(connection, task_id, "result_committed", {"result_sha": digest(raw)})

    def verify_originals(self, connection: sqlite3.Connection, row: dict) -> None:
        """发布前逐字节回读：原件缺失、被改动或撤销都不允许产出结果。"""
        if self.originals is None:
            blocked("ORIGINAL_UNAVAILABLE", 409, "服务未配置原件存储，无法核对来源")
        assets = connection.execute(
            "SELECT asset_id,sha256,storage_state FROM assessment_assets "
            "WHERE assessment_id=? AND storage_state='stored' ORDER BY source_index",
            (row["assessment_id"],),
        ).fetchall()
        if not assets:
            blocked("ORIGINAL_UNAVAILABLE", 409, "该测评没有可核对的原件")
        for asset in assets:
            try:
                self.originals.read(asset["sha256"])
            except APIError as error:
                raise APIError(409, "ORIGINAL_UNAVAILABLE", error.message) from None

    def verify_result(
        self, connection: sqlite3.Connection, row: dict, result: ProgrammingResult
    ) -> None:
        """结论必须落在本次测评已入库的证据上；对不上的整条拒绝，不保存。"""
        if result.assessment_id != row["assessment_id"]:
            blocked("AI_RESULT_SOURCE_CONFLICT", 409, "结果绑定的测评与任务不一致")
        if result.source_snapshot_id != row["snapshot_id"]:
            blocked("AI_RESULT_SOURCE_CONFLICT", 409, "结果绑定的快照与任务不一致")
        assets = {
            item["asset_id"]: item["sha256"]
            for item in connection.execute(
                "SELECT asset_id,sha256 FROM assessment_assets WHERE assessment_id=?",
                (row["assessment_id"],),
            ).fetchall()
        }
        if set(result.asset_sha256) != set(assets.values()):
            blocked("AI_RESULT_SOURCE_CONFLICT", 409, "结果引用的资产集合与测评不一致")
        for finding in result.findings:
            for evidence in finding.evidence:
                if assets.get(evidence.asset_id) is None:
                    blocked("AI_EVIDENCE_NOT_FOUND", 409, "结论引用了不属于该测评的资产")
                participant = connection.execute(
                    "SELECT student_pk,source_asset_id FROM assessment_participants "
                    "WHERE assessment_id=? AND external_key=?",
                    (row["assessment_id"], evidence.student_key),
                ).fetchone()
                if participant is None or participant["source_asset_id"] != evidence.asset_id:
                    blocked("AI_EVIDENCE_NOT_FOUND", 409, "结论引用的学生与来源包不匹配")
                submission = connection.execute(
                    "SELECT submission_id,source_index FROM submissions WHERE assessment_id=? "
                    "AND student_pk=? AND problem_id=(SELECT problem_id FROM problems "
                    "WHERE assessment_id=? AND label=?)",
                    (
                        row["assessment_id"],
                        participant["student_pk"],
                        row["assessment_id"],
                        evidence.problem_label,
                    ),
                ).fetchone()
                if submission is None:
                    blocked("AI_EVIDENCE_NOT_FOUND", 409, "结论引用的题目没有对应提交")
                if evidence.source_index is not None and (
                    evidence.source_index != submission["source_index"]
                ):
                    blocked("AI_EVIDENCE_NOT_FOUND", 409, "结论引用的题块位置与入库记录不一致")
                if evidence.source_row is not None or evidence.test_index is not None:
                    observation = connection.execute(
                        "SELECT test_index,source_row FROM test_observations "
                        "WHERE submission_id=? ORDER BY test_index",
                        (submission["submission_id"],),
                    ).fetchall()
                    match = [
                        item
                        for item in observation
                        if (
                            evidence.test_index is None or item["test_index"] == evidence.test_index
                        )
                        and (
                            evidence.source_row is None or item["source_row"] == evidence.source_row
                        )
                    ]
                    if not match:
                        blocked("AI_EVIDENCE_NOT_FOUND", 409, "结论引用的测试点与入库记录不一致")

    def fail(self, task_id: str, code: str) -> None:
        state = (
            "cancelled"
            if code == "AI_TASK_CANCELLED"
            else "timed_out"
            if code == "AI_TASK_TIMEOUT"
            else "failed"
        )
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE ai_programming_tasks SET state=?,error_code=?,result_json=NULL,"
                "result_sha=NULL WHERE task_id=? AND state IN ('queued','running')",
                (state, code, task_id),
            )
            self.event(connection, task_id, "stopped_without_result", {"code": code})

    def view(self, actor: Principal, task_id: str) -> ProgrammingTaskView:
        with self.database.transaction() as connection:
            row = self._row(connection, task_id)
            self.check(connection, row, actor)
        result = None
        if row["state"] == "completed":
            with self.database.transaction() as connection:
                current = self._row(connection, task_id)
                if (
                    current["result_json"] is None
                    or digest(json.loads(current["result_json"])) != current["result_sha"]
                ):
                    blocked("DATA_INTEGRITY_ERROR", 503, "已保存的结果与摘要不一致")
                result = ProgrammingResult.model_validate(json.loads(current["result_json"]))
        budget = json.loads(row["budget_json"])
        origin = "http_mock" if row["purpose"] == PURPOSE_MOCK else "none"
        return ProgrammingTaskView(
            task_id=task_id,
            assessment_id=row["assessment_id"],
            source_snapshot_id=row["snapshot_id"],
            purpose="programming_error_investigation",
            state=row["state"],
            error_code=row["error_code"],
            created_at=datetime.fromtimestamp(row["created"], UTC).isoformat(),
            deadline_at=datetime.fromtimestamp(row["deadline"], UTC).isoformat(),
            expires_at=datetime.fromtimestamp(row["expires"], UTC).isoformat(),
            execution_origin=origin,
            real_qwen="not_run",
            production_enabled=False,
            tool_attempts=row["tool_attempts"],
            model_requests=budget.get("request_count", 0),
            result=result,
        )

    def cancel(self, actor: Principal, task_id: str) -> ProgrammingTaskView:
        with self.database.transaction(write=True) as connection:
            row = self._row(connection, task_id)
            self.check(connection, row, actor)
            if row["state"] in ("queued", "running"):
                connection.execute(
                    "UPDATE ai_programming_tasks SET state='cancelled',cancelled=1,"
                    "error_code='AI_TASK_CANCELLED',result_json=NULL,result_sha=NULL "
                    "WHERE task_id=?",
                    (task_id,),
                )
                self.event(
                    connection, task_id, "stopped_without_result", {"code": "AI_TASK_CANCELLED"}
                )
        return self.view(actor, task_id)

    def revoke(self, assessment_id: str, snapshot_id: str | None = None) -> int:
        """内部/运维入口：撤销授权即清空未发布结果，已发布结果标记为 revoked。"""
        with self.database.transaction(write=True) as connection:
            rows = connection.execute(
                "SELECT grant_id FROM ai_programming_grants WHERE assessment_id=? "
                "AND (? IS NULL OR snapshot_id=?)",
                (assessment_id, snapshot_id, snapshot_id),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE ai_programming_grants SET revoked=1 WHERE grant_id=?",
                    (row["grant_id"],),
                )
                connection.execute(
                    "UPDATE ai_programming_tasks SET state='revoked',cancelled=1,"
                    "error_code='AI_PURPOSE_REVOKED',result_json=NULL,result_sha=NULL "
                    "WHERE grant_id=? AND state IN ('queued','running','completed')",
                    (row["grant_id"],),
                )
                connection.execute(
                    "INSERT INTO ai_programming_events (task_id,sequence,phase,payload,created_at) "
                    "SELECT t.task_id,COALESCE((SELECT MAX(sequence) FROM ai_programming_events e "
                    "WHERE e.task_id=t.task_id),0)+1,'revoked','{}',? "
                    "FROM ai_programming_tasks t WHERE t.grant_id=?",
                    (stamp(datetime.now(UTC)), row["grant_id"]),
                )
        return len(rows)

    def new_secret(self) -> str:
        return secrets.token_hex(8)
