-- B-03C：材料身份从「批次 + 包内路径」改为「来源包 + 包内路径」。
-- 两个 ZIP 合法地包含同名内部路径（例如两班各自的 答卷.html）时，
-- 旧约束 UNIQUE (assessment_id, entry) 会把它变成唯一键冲突，整个导入回滚。
-- 只重建 assessment_materials 一张表：新表同位建好、整体搬运、再替换，
-- 历史行一条不丢；009 已发布不动，新编号按 010/011 预留之后的 013 继续。

-- 中间表名带引号是有意的：就绪检查会从迁移文本里抽取 CREATE 出来的对象名，
-- 重建用的中间表最终并不存在，不能被当成"最终结构应有的对象"。
CREATE TABLE "assessment_materials_013" (
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
    -- asset_id 是该材料的来源包；孪生路径靠它区分，未绑定时允许为空
    asset_id TEXT REFERENCES assessment_assets(asset_id),
    UNIQUE (assessment_id, asset_id, entry)
) STRICT;

INSERT INTO "assessment_materials_013" (
    material_id, assessment_id, entry, category, basis, signals, needs_confirmation, asset_id
)
SELECT material_id, assessment_id, entry, category, basis, signals, needs_confirmation, asset_id
FROM assessment_materials;

DROP TABLE assessment_materials;
ALTER TABLE "assessment_materials_013" RENAME TO assessment_materials;
CREATE INDEX materials_assessment ON assessment_materials (assessment_id);
