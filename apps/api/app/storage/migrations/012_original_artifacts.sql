-- B-03B 原件与来源快照：仍然只做加法，不重建表、不改 009 及更早的迁移。
-- 010 / 011 按 DECISIONS_V14 预留给 findings / code_features，这里用 012。

-- 原件保存状态：stored 表示受控存储里有可校验的原件；
-- absent 表示历史批次或未启用原件存储（结构化证据仍可读，原件回读会被拒绝）；
-- revoked 表示来源已撤销，不得再作为证据。
ALTER TABLE assessment_assets ADD COLUMN storage_state TEXT NOT NULL DEFAULT 'absent'
    CHECK (storage_state IN ('stored','absent','revoked'));

-- 来源快照标识：绑定测评与它的资产集合（entry/kind/sha256/byte_size/source_index）。
-- 由服务端按固定规则计算，重启前后一致；资产集合变化后旧快照不再匹配。
ALTER TABLE assessments ADD COLUMN source_snapshot_id TEXT;
