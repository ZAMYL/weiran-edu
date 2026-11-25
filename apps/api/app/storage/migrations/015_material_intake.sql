-- B-02-S1 资料确认：只存结构快照与教师确认，不存原始字节。
-- 009 的 assessment_* 仍是正式测评层；这里只在它前面加一层可确认的资料记录，
-- 不改任何已发布迁移，也不重建旧表。

CREATE TABLE material_records (
    material_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    source_id TEXT NOT NULL CHECK (length(source_id) BETWEEN 8 AND 96),
    owner_teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    -- 上传时可以为空：确认前只有本人可见，确认时才绑定教学空间。
    data_scope_id TEXT REFERENCES data_scopes(data_scope_id),
    file_name TEXT NOT NULL CHECK (length(file_name) BETWEEN 1 AND 200),
    kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 32),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    -- 只存表/列结构与候选含义，不存任何单元格内容。
    plan_json TEXT NOT NULL,
    plan_version TEXT NOT NULL CHECK (plan_version = 'material.v1'),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    created_at TEXT NOT NULL,
    UNIQUE (sha256, owner_teacher_id)
) STRICT;

-- 只追加的确认审计：谁在什么时候把哪个位置确认为什么，改主意就新增一条。
CREATE TABLE material_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES material_records(material_id),
    kind TEXT NOT NULL CHECK (kind IN ('role', 'field')),
    table_ref TEXT NOT NULL CHECK (length(table_ref) BETWEEN 1 AND 32),
    column_ref TEXT CHECK (column_ref IS NULL OR length(column_ref) BETWEEN 1 AND 32),
    value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 32),
    confirmed_by TEXT NOT NULL REFERENCES teachers(teacher_id),
    confirmed_at TEXT NOT NULL,
    basis TEXT NOT NULL DEFAULT 'teacher_review' CHECK (length(basis) BETWEEN 1 AND 64),
    CHECK ((kind = 'role') = (column_ref IS NULL)),
    UNIQUE (material_id, kind, table_ref, column_ref)
) STRICT;

-- SQLite 的 UNIQUE 把 NULL 视为互不相同，角色行（column_ref IS NULL）另用部分索引兜住。
CREATE UNIQUE INDEX material_confirmations_role_key
    ON material_confirmations (material_id, table_ref) WHERE kind = 'role';

CREATE INDEX material_records_owner
    ON material_records (owner_teacher_id, created_at, material_id);
CREATE INDEX material_confirmations_material
    ON material_confirmations (material_id, confirmed_at);

-- 桥：资料真的进入某次测评时，包级原件能指回它来自哪份已确认的资料。
ALTER TABLE assessment_assets ADD COLUMN material_id TEXT
    REFERENCES material_records(material_id);
