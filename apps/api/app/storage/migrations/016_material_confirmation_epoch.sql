-- B-02-S1 加固：识别快照指纹、确认失效标记与同空间共享读所需的索引。
-- 只做加法，不改已发布的 015，也不重建 material_records / material_confirmations。

-- 同一份字节重新上传时，识别结构可能已经变化（例如解析器升级）。
-- 没有指纹就无法判断旧确认描述的是不是上一版定位，因此这里记录 plan_json 的摘要。
ALTER TABLE material_records ADD COLUMN plan_sha256 TEXT;

-- 快照一变，旧确认整体失效但绝不删除：审计仍在，读取与重放不再把它们当成现行结论。
ALTER TABLE material_confirmations ADD COLUMN stale INTEGER NOT NULL DEFAULT 0
    CHECK (stale IN (0, 1));

-- 同空间其他教师读取：按 data_scope_id 找记录时走这条部分索引。
CREATE INDEX material_records_scope
    ON material_records (data_scope_id, created_at, material_id)
    WHERE data_scope_id IS NOT NULL;
