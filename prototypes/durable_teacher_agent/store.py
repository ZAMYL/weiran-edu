"""Operator-installed synthetic task tables in the SAME authorized SQLite database.

Campaign uniqueness is permanent. Interrupted work never resumes or refunds. No raw
workbook, student row, comment or API credential is copied into these tables.
"""

import json
import sqlite3
import time
from collections.abc import Callable
from uuid import uuid4

from app.ai.edu.discovery.models import AgentError
from app.core.errors import APIError
from app.edu.auth import Principal
from app.edu.reads import BatchReader
from app.edu.reviews import require_owner

from prototypes.research_agent_v2.goals import understand
from prototypes.teacher_agent_bridge.models import InvestigationResult
from prototypes.teacher_agent_bridge.provider import ALIASES, DatabaseProvider, digest

from .models import (
    PROFILE_RESULT_CONTRACT,
    RESULT_CONTRACT_KEY,
    STANDARD_RESULT_CONTRACT,
    ProfileInvestigationResult,
    TaskCreate,
)


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def blocked(code: str, status: int = 409) -> None:
    raise APIError(status, code, "调查授权、任务状态或核验条件不满足，请重新读取状态")


class TaskStore:
    def __init__(self, provider: DatabaseProvider, *, clock: Callable[[], float] = time.time):
        self.provider = provider
        self.database = provider.service.database
        self.clock = clock
        self.reader = BatchReader(self.database, clock=provider.service.clock)

    def initialize(self) -> None:
        """Explicit operator action, never a migration in an ordinary teacher request."""
        statements = (
            "CREATE TABLE IF NOT EXISTS ai_synthetic_grants ("
            "grant_id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL REFERENCES teachers, "
            "batch_id TEXT NOT NULL REFERENCES batches, snapshot_id TEXT NOT NULL, "
            "source_sha TEXT NOT NULL, purpose TEXT NOT NULL CHECK(purpose='synthetic_mock'), "
            "expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, "
            "UNIQUE(teacher_id,batch_id,snapshot_id)) STRICT",
            "CREATE TABLE IF NOT EXISTS ai_synthetic_tasks ("
            "task_id TEXT PRIMARY KEY, grant_id TEXT NOT NULL UNIQUE REFERENCES "
            "ai_synthetic_grants, "
            "teacher_id TEXT NOT NULL, credential_sha TEXT NOT NULL, "
            "idempotency_key TEXT NOT NULL, request_sha TEXT NOT NULL, request_json TEXT NOT NULL, "
            "campaign_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL, error_code TEXT, "
            "created REAL NOT NULL, deadline REAL NOT NULL, expires REAL NOT NULL, "
            "cancelled INTEGER NOT NULL DEFAULT 0, tool_attempts INTEGER NOT NULL DEFAULT 0, "
            "budget_json TEXT NOT NULL DEFAULT '{}', disclosure_json TEXT NOT NULL DEFAULT '[]', "
            "proof_json TEXT, result_json TEXT, result_sha TEXT, review_json TEXT, "
            "UNIQUE(teacher_id,idempotency_key)) STRICT",
            "CREATE TABLE IF NOT EXISTS ai_synthetic_events ("
            "task_id TEXT NOT NULL REFERENCES ai_synthetic_tasks, sequence INTEGER NOT NULL, "
            "phase TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,sequence)) STRICT",
        )
        with self.database.transaction(write=True) as c:
            for statement in statements:
                c.execute(statement)

    def grant(
        self, actor: Principal, batch_id: str, snapshot_id: str, *, lifetime: int = 3600
    ) -> str:
        if not 1 <= lifetime <= 3600:
            raise ValueError("invalid_synthetic_grant_lifetime")
        _, manifest = self.provider.capture(actor, batch_id, snapshot_id)
        from hashlib import sha256

        fingerprint = sha256(manifest).hexdigest()
        with self.database.transaction(write=True) as c:
            # Auth is checked inside the same write transaction as the durable binding.
            require_owner(self.reader._batch(c, actor, batch_id, snapshot_id), actor)
            row = c.execute(
                "SELECT * FROM ai_synthetic_grants WHERE teacher_id=? AND batch_id=? "
                "AND snapshot_id=?",
                (actor.teacher_id, batch_id, snapshot_id),
            ).fetchone()
            if row:
                if row["source_sha"] != fingerprint:
                    blocked("AI_SOURCE_CONFLICT")
                return row["grant_id"]  # Never renew or reset a consumed/revoked grant.
            gid = str(uuid4())
            c.execute(
                "INSERT INTO ai_synthetic_grants VALUES (?,?,?,?,?,'synthetic_mock',?,0)",
                (
                    gid,
                    actor.teacher_id,
                    batch_id,
                    snapshot_id,
                    fingerprint,
                    self.clock() + lifetime,
                ),
            )
            return gid

    def revoke(self, grant_id: str) -> None:
        with self.database.transaction(write=True) as c:
            c.execute("UPDATE ai_synthetic_grants SET revoked=1 WHERE grant_id=?", (grant_id,))
            c.execute(
                "UPDATE ai_synthetic_tasks SET "
                "state='revoked',cancelled=1,error_code='AI_PURPOSE_REVOKED',"
                "result_json=NULL,result_sha=NULL WHERE grant_id=?",
                (grant_id,),
            )

    def _row(self, c: sqlite3.Connection, task_id: str) -> dict:
        row = c.execute(
            "SELECT t.*,g.batch_id,g.snapshot_id,g.source_sha,g.purpose,g.revoked,"
            "g.expires AS grant_expires FROM ai_synthetic_tasks t "
            "JOIN ai_synthetic_grants g USING(grant_id) WHERE t.task_id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            blocked("RESOURCE_NOT_FOUND", 404)
        return dict(row)

    def check(
        self, c: sqlite3.Connection, row: dict, actor: Principal, *, running: bool = False
    ) -> None:
        if row["teacher_id"] != actor.teacher_id:
            blocked("RESOURCE_NOT_FOUND", 404)
        self.reader._batch(c, actor, row["batch_id"], row["snapshot_id"])
        require_owner(self.reader._batch(c, actor, row["batch_id"], row["snapshot_id"]), actor)
        # Revoking the initiating credential invalidates this campaign too.
        self.reader._batch(
            c,
            Principal(row["teacher_id"], row["credential_sha"]),
            row["batch_id"],
            row["snapshot_id"],
        )
        if row["revoked"] or row["purpose"] != "synthetic_mock":
            blocked("AI_PURPOSE_REVOKED", 403)
        if min(row["expires"], row["grant_expires"]) <= self.clock():
            blocked("AI_TASK_EXPIRED", 410)
        if running:
            if row["cancelled"] or row["state"] != "running":
                blocked("AI_TASK_CANCELLED")
            if row["deadline"] <= self.clock():
                blocked("AI_TASK_TIMEOUT")

    def authorize(self, task_id: str, actor: Principal, *, running: bool = False) -> bool:
        with self.database.transaction() as c:
            self.check(c, self._row(c, task_id), actor, running=running)
        return False

    def guard(self, task_id: str) -> bool:
        with self.database.transaction() as c:
            row = self._row(c, task_id)
            self.check(c, row, Principal(row["teacher_id"], row["credential_sha"]), running=True)
        return False

    def create(
        self, actor: Principal, batch_id: str, body: TaskCreate, *, timeout: int = 120
    ) -> tuple[dict, bool]:
        if not 1 <= timeout <= 120:
            raise ValueError("invalid_synthetic_task_timeout")
        # Purpose preflight happens before loading any score material. Repeat the
        # same checks under the write lock before a task can actually be claimed.
        with self.database.transaction() as c:
            self._task_grant(c, actor, batch_id, body.snapshot_id)
        material, _ = self.provider.capture(actor, batch_id, body.snapshot_id)
        question = body.question
        for alias, item in zip(ALIASES, material.data.items, strict=True):
            question = question.replace(item.name, alias)
        try:
            goal = understand(
                question, {"items": [{"item_id": i.item_id} for i in material.data.items]}
            )
        except AgentError:
            blocked("AI_GOAL_UNSUPPORTED", 422)
        label = {
            "priority": "优先核查",
            "relation": "关联",
            "group": "群体差异",
            "quality": "核查缺分",
            "explanation": "为什么相关",
        }[goal.kind]
        # Persist only the controlled intent/items, never the arbitrary question text.
        contract = (
            PROFILE_RESULT_CONTRACT
            if body.strategy == "adaptive" and goal.kind == "priority" and not goal.items
            else STANDARD_RESULT_CONTRACT
        )
        raw = body.model_dump(mode="json") | {
            "question": label + " " + " ".join(goal.items),
            RESULT_CONTRACT_KEY: contract,
        }
        request_sha = digest(raw)
        with self.database.transaction(write=True) as c:
            grant = self._task_grant(c, actor, batch_id, body.snapshot_id)
            old = c.execute(
                "SELECT task_id,request_sha FROM ai_synthetic_tasks WHERE teacher_id=? "
                "AND idempotency_key=?",
                (actor.teacher_id, body.idempotency_key),
            ).fetchone()
            if old:
                row = self._row(c, old["task_id"])
                self.check(c, row, actor)
                legacy = dict(raw)
                legacy.pop(RESULT_CONTRACT_KEY)
                if row["batch_id"] != batch_id or old["request_sha"] not in {
                    request_sha,
                    digest(legacy),
                }:
                    blocked("AI_IDEMPOTENCY_CONFLICT")
                return row, False
            if c.execute(
                "SELECT 1 FROM ai_synthetic_tasks WHERE grant_id=?", (grant["grant_id"],)
            ).fetchone():
                blocked("AI_CAMPAIGN_ALREADY_USED")
            tid = str(uuid4())
            campaign = "durable-" + digest((actor.teacher_id, batch_id, body.snapshot_id))[:40]
            now = self.clock()
            c.execute(
                "INSERT INTO ai_synthetic_tasks (task_id,grant_id,teacher_id,credential_sha,"
                "idempotency_key,request_sha,request_json,campaign_id,state,"
                "created,deadline,expires) "
                "VALUES (?,?,?,?,?,?,?,?,'queued',?,?,?)",
                (
                    tid,
                    grant["grant_id"],
                    actor.teacher_id,
                    actor.token_hash,
                    body.idempotency_key,
                    request_sha,
                    encode(raw),
                    campaign,
                    now,
                    now + timeout,
                    grant["expires"],
                ),
            )
            return self._row(c, tid), True

    def _task_grant(
        self, c: sqlite3.Connection, actor: Principal, batch_id: str, snapshot_id: str
    ) -> sqlite3.Row:
        batch = self.reader._batch(c, actor, batch_id, snapshot_id)
        require_owner(batch, actor)
        self.provider.require_registered_source(batch["file_sha256"])
        grant = c.execute(
            "SELECT * FROM ai_synthetic_grants WHERE teacher_id=? AND batch_id=? AND snapshot_id=?",
            (actor.teacher_id, batch_id, snapshot_id),
        ).fetchone()
        if grant is None:
            raise APIError(403, "AI_PURPOSE_REQUIRED", "这份测评尚未获得 AI 分析授权")
        if grant["revoked"] or grant["purpose"] != "synthetic_mock":
            raise APIError(403, "AI_PURPOSE_REVOKED", "本测评的 AI 分析授权已撤销")
        if grant["expires"] <= self.clock():
            raise APIError(403, "AI_PURPOSE_REQUIRED", "AI 分析授权已过期，请联系演示负责人")
        return grant

    def get(self, actor: Principal, task_id: str) -> dict:
        # Read-only fast path: normal status reads must not take BEGIN IMMEDIATE, which
        # serializes against the worker's writes and surfaces as SQLITE_BUSY/503.
        with self.database.transaction() as c:
            row = self._row(c, task_id)
            self.check(c, row, actor)
            if not (row["state"] in ("queued", "running") and row["deadline"] <= self.clock()):
                return row
        # Expired task: re-check state and deadline under a write lock before transitioning,
        # so a concurrent transition is observed exactly once.
        with self.database.transaction(write=True) as c:
            row = self._row(c, task_id)
            self.check(c, row, actor)
            if row["state"] in ("queued", "running") and row["deadline"] <= self.clock():
                c.execute(
                    "UPDATE ai_synthetic_tasks SET "
                    "state='timed_out',cancelled=1,error_code='AI_TASK_TIMEOUT' WHERE task_id=?",
                    (task_id,),
                )
                row = self._row(c, task_id)
            return row

    def start(self, task_id: str) -> dict:
        with self.database.transaction(write=True) as c:
            row = self._row(c, task_id)
            self.check(c, row, Principal(row["teacher_id"], row["credential_sha"]))
            if row["state"] != "queued" or row["deadline"] <= self.clock():
                blocked("AI_TASK_NOT_RUNNABLE")
            c.execute("UPDATE ai_synthetic_tasks SET state='running' WHERE task_id=?", (task_id,))
            return self._row(c, task_id)

    @staticmethod
    def event(c: sqlite3.Connection, task_id: str, phase: str, payload: dict) -> None:
        seq = c.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM ai_synthetic_events WHERE task_id=?",
            (task_id,),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO ai_synthetic_events VALUES (?,?,?,?)",
            (task_id, seq, phase, encode(payload)),
        )

    def audit(self, task_id: str, phase: str, executor, entry: dict) -> None:
        with self.database.transaction(write=True) as c:
            row = self._row(c, task_id)
            self.check(c, row, executor.source.envelope.actor, running=True)
            if phase == "attempt":
                if row["tool_attempts"] >= 5:
                    blocked("AI_TOOL_BUDGET_EXHAUSTED")
                c.execute(
                    "UPDATE ai_synthetic_tasks SET tool_attempts=tool_attempts+1 WHERE task_id=?",
                    (task_id,),
                )
            self.event(c, task_id, "tool_" + phase, entry)
            # Conservative history survives failure, cancellation and process death.
            disclosure = [sorted(group) for group in executor.released]
            c.execute(
                "UPDATE ai_synthetic_tasks SET disclosure_json=? WHERE task_id=?",
                (encode(disclosure), task_id),
            )

    def budget(self, task_id: str, snapshot: dict) -> None:
        with self.database.transaction(write=True) as c:
            row = self._row(c, task_id)
            self.check(c, row, Principal(row["teacher_id"], row["credential_sha"]), running=True)
            if (
                snapshot["campaign_id"] != row["campaign_id"]
                or snapshot["source_fingerprints"]
                != {"authorized-db-" + row["batch_id"]: row["source_sha"]}
                or snapshot["origin"] != "http_mock"
            ):
                blocked("AI_BUDGET_SCOPE_CONFLICT")
            old = json.loads(row["budget_json"])
            if (
                snapshot["request_count"] < old.get("request_count", 0)
                or snapshot["request_count"] > 6
            ):
                blocked("AI_BUDGET_RESET_FORBIDDEN")
            self.event(c, task_id, "model_budget", snapshot)
            c.execute(
                "UPDATE ai_synthetic_tasks SET budget_json=? WHERE task_id=?",
                (encode(snapshot), task_id),
            )

    def finish(
        self, task_id: str, actor: Principal, result: InvestigationResult, proof: dict
    ) -> None:
        raw = result.model_dump(mode="json")
        with self.database.transaction(write=True) as c:
            row = self._row(c, task_id)
            self.check(c, row, actor, running=True)
            profile_expected = self.profile_result_expected(row, proof)
            if isinstance(result, ProfileInvestigationResult) is not profile_expected:
                blocked("AI_PROFILE_PROOF_CONFLICT", 503)
            c.execute(
                "UPDATE ai_synthetic_tasks SET "
                "state='completed',result_json=?,result_sha=?,proof_json=?,disclosure_json=? "
                "WHERE task_id=?",
                (encode(raw), digest(raw), encode(proof), encode(proof["released"]), task_id),
            )
            self.event(c, task_id, "result_committed", {"result_sha": digest(raw)})

    @staticmethod
    def profile_result_expected(row: dict, proof: dict) -> bool:
        request = json.loads(row["request_json"])
        if not isinstance(request, dict) or digest(request) != row["request_sha"]:
            blocked("DATA_INTEGRITY_ERROR", 503)
        contract = request.get(RESULT_CONTRACT_KEY)
        has_profile = "profile" in proof
        has_tool_proof = "tool_proof" in proof
        if contract is None:
            if has_profile is not has_tool_proof:
                blocked("AI_PROFILE_PROOF_CONFLICT", 503)
            # Historical tasks predate the explicit contract. Their server proof
            # remains authoritative: ordinary results stay ordinary, while the
            # original #81 profile proof still requires a profile result.
            return has_profile
        if contract not in {PROFILE_RESULT_CONTRACT, STANDARD_RESULT_CONTRACT}:
            blocked("AI_PROFILE_PROOF_CONFLICT", 503)
        profile_expected = contract == PROFILE_RESULT_CONTRACT
        if has_profile is not profile_expected or has_tool_proof is not profile_expected:
            blocked("AI_PROFILE_PROOF_CONFLICT", 503)
        return profile_expected

    def fail(self, task_id: str, code: str) -> None:
        state = (
            "cancelled"
            if code == "AI_TASK_CANCELLED"
            else "timed_out"
            if code == "AI_TASK_TIMEOUT"
            else "failed"
        )
        with self.database.transaction(write=True) as c:
            c.execute(
                "UPDATE ai_synthetic_tasks SET "
                "state=?,error_code=?,result_json=NULL,result_sha=NULL "
                "WHERE task_id=? AND state IN ('queued','running')",
                (state, code, task_id),
            )
            self.event(c, task_id, "stopped_without_result", {"code": code})

    def cancel(self, actor: Principal, task_id: str) -> dict:
        with self.database.transaction(write=True) as c:
            row = self._row(c, task_id)
            self.check(c, row, actor)
            if row["state"] in ("queued", "running"):
                c.execute(
                    "UPDATE ai_synthetic_tasks SET "
                    "cancelled=1,state='cancelled',error_code='AI_TASK_CANCELLED' WHERE "
                    "task_id=?",
                    (task_id,),
                )
            return self._row(c, task_id)
