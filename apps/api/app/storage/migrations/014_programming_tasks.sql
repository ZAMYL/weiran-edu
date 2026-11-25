-- B-04C：编程测评 Agent 任务的正式入口表。
-- 与旧五维任务（ai_synthetic_* 挂在 batches 上）分开：这里以 assessments 为来源，
-- 不复用 batch_id，也不伪造五维结果契约。表结构只做新增，不动任何已发布迁移。

CREATE TABLE ai_programming_grants (
    grant_id TEXT PRIMARY KEY,
    teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 64),
    source_sha TEXT NOT NULL CHECK (length(source_sha) = 64),
    purpose TEXT NOT NULL CHECK (purpose IN ('assessment_mock', 'assessment_live')),
    expires REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (teacher_id, assessment_id, snapshot_id)
) STRICT;

CREATE TABLE ai_programming_tasks (
    task_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL UNIQUE REFERENCES ai_programming_grants(grant_id),
    teacher_id TEXT NOT NULL,
    credential_sha TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha TEXT NOT NULL,
    request_json TEXT NOT NULL,
    campaign_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN (
        'queued', 'running', 'completed', 'failed', 'cancelled', 'timed_out', 'revoked'
    )),
    error_code TEXT,
    created REAL NOT NULL,
    deadline REAL NOT NULL,
    expires REAL NOT NULL,
    cancelled INTEGER NOT NULL DEFAULT 0 CHECK (cancelled IN (0, 1)),
    tool_attempts INTEGER NOT NULL DEFAULT 0 CHECK (tool_attempts >= 0),
    budget_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    result_sha TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (teacher_id, idempotency_key)
) STRICT;

CREATE TABLE ai_programming_events (
    task_id TEXT NOT NULL REFERENCES ai_programming_tasks(task_id),
    sequence INTEGER NOT NULL,
    phase TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, sequence)
) STRICT;

CREATE INDEX programming_tasks_assessment ON ai_programming_grants (assessment_id);
CREATE INDEX programming_tasks_teacher ON ai_programming_tasks (teacher_id, created);
