-- B-03 可信证据持久化：把解析器已经产出、但 007 丢掉的字段补齐。
-- 只做加法：不重建表、不改 006/007/008，历史行靠默认值与 NULL 兼容。
-- 历史行的 status_class / verdict_class 为 NULL，读取侧按原文重新归类，
-- 归不了类的一律 UNKNOWN，绝不计入失败。

-- 包级资产：来源绑定的最小单位。assessment_assets 与 006 的 source_assets 不同，
-- 后者必须挂在 imports 上，答题卡链路没有 import，因此单独建表，不伪造 import。
CREATE TABLE assessment_assets (
    asset_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    entry TEXT NOT NULL CHECK (length(entry) BETWEEN 1 AND 512),
    kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 32),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    source_index INTEGER NOT NULL CHECK (source_index >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (assessment_id, entry)
) STRICT;

-- 材料角色与判定依据：needs_confirmation 的材料不得被静默计入正式统计。
CREATE TABLE assessment_materials (
    material_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    entry TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN (
        'STUDENT_PAPER','STANDARD_ANSWER','BLANK_PAPER',
        'TEST_DATA','PROBLEM_STATEMENT','UNKNOWN'
    )),
    basis TEXT NOT NULL,
    signals TEXT NOT NULL DEFAULT '',
    needs_confirmation INTEGER NOT NULL DEFAULT 0 CHECK (needs_confirmation IN (0, 1)),
    asset_id TEXT REFERENCES assessment_assets(asset_id),
    UNIQUE (assessment_id, entry)
) STRICT;

-- 解析诊断与被排除的记录（skipped 也进这张表，用 code 区分）。
CREATE TABLE assessment_diagnostics (
    diagnostic_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    code TEXT NOT NULL CHECK (length(code) BETWEEN 1 AND 64),
    entry TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX assets_assessment ON assessment_assets (assessment_id);
CREATE INDEX materials_assessment ON assessment_materials (assessment_id);
CREATE INDEX diagnostics_assessment ON assessment_diagnostics (assessment_id, code);

-- 题序依据 + 复核状态：有疑义时人数只能标为待确认，不能当作正式统计发布。
ALTER TABLE assessments ADD COLUMN order_basis TEXT NOT NULL DEFAULT 'NATURAL_SORT';
ALTER TABLE assessments ADD COLUMN review_state TEXT NOT NULL DEFAULT 'confirmed'
    CHECK (review_state IN ('confirmed', 'needs_review'));
ALTER TABLE assessments ADD COLUMN review_reasons TEXT NOT NULL DEFAULT '';

-- 参与者的表头事实：开始/结束时间、时长、卷面分（分数按百分之一分存，与 submissions 一致）。
ALTER TABLE assessment_participants ADD COLUMN total_score_units INTEGER;
ALTER TABLE assessment_participants ADD COLUMN score_units INTEGER;
ALTER TABLE assessment_participants ADD COLUMN started_at TEXT;
ALTER TABLE assessment_participants ADD COLUMN finished_at TEXT;
ALTER TABLE assessment_participants ADD COLUMN duration_minutes INTEGER;
ALTER TABLE assessment_participants ADD COLUMN source_asset_id TEXT
    REFERENCES assessment_assets(asset_id);

-- 同题满分冲突：不同文件对同一题给出不同满分时，不得生成可信得分率。
ALTER TABLE problems ADD COLUMN total_score_conflict INTEGER NOT NULL DEFAULT 0
    CHECK (total_score_conflict IN (0, 1));

-- 提交：代码三态、截断标记、编译输出、来源定位。
ALTER TABLE submissions ADD COLUMN code_state TEXT NOT NULL DEFAULT 'PRESENT'
    CHECK (code_state IN ('PRESENT', 'UNANSWERED', 'MISSING'));
ALTER TABLE submissions ADD COLUMN code_truncated INTEGER NOT NULL DEFAULT 0
    CHECK (code_truncated IN (0, 1));
ALTER TABLE submissions ADD COLUMN compiler_output TEXT;
ALTER TABLE submissions ADD COLUMN compiler_output_truncated INTEGER NOT NULL DEFAULT 0
    CHECK (compiler_output_truncated IN (0, 1));
ALTER TABLE submissions ADD COLUMN verdict_class TEXT
    CHECK (verdict_class IN ('PASS', 'FAIL', 'PARTIAL', 'NOT_ATTEMPTED', 'UNKNOWN'));
ALTER TABLE submissions ADD COLUMN source_asset_id TEXT REFERENCES assessment_assets(asset_id);
ALTER TABLE submissions ADD COLUMN source_entry TEXT;
ALTER TABLE submissions ADD COLUMN source_index INTEGER;

-- 测试点：状态类别冻结、精确耗时与原始文本。
-- duration_ms 仍是取整展示值；精确值在 duration_us（微秒），原样文本在 duration_text。
ALTER TABLE test_observations ADD COLUMN status_known INTEGER NOT NULL DEFAULT 1
    CHECK (status_known IN (0, 1));
ALTER TABLE test_observations ADD COLUMN status_class TEXT
    CHECK (status_class IN ('PASS', 'FAIL', 'PARTIAL', 'NOT_ATTEMPTED', 'UNKNOWN'));
ALTER TABLE test_observations ADD COLUMN duration_us INTEGER CHECK (duration_us >= 0);
ALTER TABLE test_observations ADD COLUMN duration_text TEXT NOT NULL DEFAULT '';
ALTER TABLE test_observations ADD COLUMN memory_text TEXT NOT NULL DEFAULT '';
ALTER TABLE test_observations ADD COLUMN source_row INTEGER;
